from mitmproxy import http
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history
import controller
from bank_filter import is_bank_flow, ensure_response_decoded

try:
    from panel_bridge import PANEL_PORT as _PANEL_PORT
except Exception:
    _PANEL_PORT = 8082


def _panel_fetch_origin() -> str:
    """Origin для fetch из WebView Т‑Банка: на телефоне 127.0.0.1 — это сам телефон. Задайте panel_fetch_origin или panel_http_port в config.json (8083 для run.py/panel_server)."""
    env = (os.environ.get("TBANK_PANEL_FETCH_ORIGIN") or "").strip()
    if env:
        return env.rstrip("/")
    raw = (controller.config.get("panel_fetch_origin") or "").strip()
    if raw:
        return raw.rstrip("/")
    port = controller.config.get("panel_http_port")
    if port is not None and str(port).strip() != "":
        try:
            return f"http://127.0.0.1:{int(port)}"
        except (TypeError, ValueError):
            pass
    return f"http://127.0.0.1:{_PANEL_PORT}"


def _effective_balance_for_display() -> float:
    base = float(((controller.config.get("balance") or {}).get("new_balance")) or 0)
    try:
        adj = history.compute_manual_balance_adjustment()
        return round(base + float(adj), 2)
    except Exception:
        return round(base, 2)


def _manual_ops_payload():
    history.ensure_manual_operations_fresh()
    items = []
    for op_id, op in history.manual_operations.items():
        if op_id in history.hidden_operations:
            continue
        if not history.is_current_month(op.get("date", "")):
            continue
        items.append(
            {
                "id": op_id,
                "date": op.get("date", ""),
                "amount": float(op.get("amount") or 0),
                "type": op.get("type") or "Debit",
                "title": op.get("title") or "",
                "subtitle": op.get("subtitle") or "",
                "description": op.get("description") or "",
                "bank": op.get("bank") or "",
                "bank_preset": (op.get("bank_preset") or "custom").lower(),
                "phone": op.get("phone") or "",
                "requisite_phone": op.get("requisite_phone") or op.get("phone") or "",
                "sender_name": op.get("sender_name") or "",
                "requisite_sender_name": op.get("requisite_sender_name") or op.get("sender_name") or "",
                "card_number": op.get("card_number") or "",
            }
        )
    skip_ids = set(history.manual_operations.keys())
    for row in history._fake_transfer_ops_for_panel_month(skip_ids):
        if row.get("id") in history.hidden_operations:
            continue
        items.append(
            {
                "id": row["id"],
                "date": row.get("date") or "",
                "amount": float(row.get("amount") or 0),
                "type": row.get("type") or "Debit",
                "title": row.get("title") or row.get("desc") or "",
                "subtitle": row.get("subtitle") or "",
                "description": row.get("description") or "",
                "bank": row.get("bank") or "",
                "bank_preset": (row.get("bank_preset") or "sbp").lower(),
                "phone": row.get("phone") or row.get("requisite_phone") or "",
                "requisite_phone": row.get("requisite_phone") or row.get("phone") or "",
                "sender_name": row.get("sender_name") or "",
                "requisite_sender_name": row.get("requisite_sender_name") or row.get("sender_name") or "",
                "card_number": row.get("card_number") or "",
            }
        )
    items.sort(key=lambda x: history.date_str_to_millis(x.get("date", "")), reverse=True)
    return items


def _detail_ops_by_id_payload() -> dict:
    """Снимок по id для ?operationId= (включая скрытые в ленте), чтобы не подставлять чужой телефон из DOM."""
    history.ensure_manual_operations_fresh()
    out = {}
    for oid, op in history.manual_operations.items():
        if not history.is_current_month(op.get("date", "")):
            continue
        oid_s = str(oid)
        out[oid_s] = {
            "type": op.get("type") or "Debit",
            "title": (op.get("title") or "").strip(),
            "description": (op.get("description") or "").strip(),
            "requisite_phone": (op.get("requisite_phone") or op.get("phone") or "").strip(),
            "phone": (op.get("phone") or "").strip(),
            "requisite_sender_name": (op.get("requisite_sender_name") or op.get("sender_name") or "").strip(),
            "sender_name": (op.get("sender_name") or "").strip(),
            "card_number": (op.get("card_number") or "").strip(),
            "bank_preset": (op.get("bank_preset") or "custom").lower(),
            "bank": (op.get("bank") or "").strip(),
            "manual": True,
        }
    skip_ids = set(history.manual_operations.keys())
    for row in history._fake_transfer_ops_for_panel_month(skip_ids):
        oid_s = str(row.get("id") or "").strip()
        if not oid_s:
            continue
        out[oid_s] = {
            "type": row.get("type") or "Debit",
            "title": (row.get("title") or row.get("desc") or "").strip(),
            "description": (row.get("description") or "").strip(),
            "requisite_phone": (row.get("requisite_phone") or row.get("phone") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "requisite_sender_name": (row.get("requisite_sender_name") or row.get("sender_name") or "").strip(),
            "sender_name": (row.get("sender_name") or "").strip(),
            "card_number": (row.get("card_number") or "").strip(),
            "bank_preset": (row.get("bank_preset") or "sbp").lower(),
            "bank": (row.get("bank") or "").strip(),
            "fake_transfer": True,
        }
    return out


def _preset_payload():
    raw = history.load_merchant_presets() or {}
    out = {}
    for key, block in raw.items():
        if not isinstance(block, dict):
            continue
        merchant = block.get("merchant") or {}
        if not isinstance(merchant, dict):
            merchant = {}
        out[str(key).lower()] = {
            "name": merchant.get("name") or "",
            "logo": merchant.get("logo") or merchant.get("logoUrl") or merchant.get("image") or "",
        }
    return out


def _read_html_sidecar(filename: str) -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


# Снимок DOM Т‑Банка (карточка «Перевод» / Black) — `_reference_account_molecule.html` + оболочка mobile-pumba.
_ACCOUNT_CARD_MANUAL_INNER_HTML = (
    '<div data-qa-type="mobile-pumba-account-operation" data-guid="manual-operation-card">'
    + _read_html_sidecar("_reference_account_molecule.html")
    + '<div data-qa-type="uikit/NotificationStack" class="abhURjxRW" data-component-type="platform-ui"></div></div><div><div class="abeiuVKPb"></div></div>'
)

_BANK_DETAILS_MANUAL_INNER_HTML = _read_html_sidecar("_reference_bank_details_inner.html")

# Как на витрине: сначала accountCardsShown-wrapper, внутри — ряд с --gaps и mobile-pumba-account-operation.
_ACCOUNT_CARDS_MANUAL_SHELL_HTML = (
    '<div data-qa-type="accountCardsShown-wrapper" class="abVXAIVX5" data-component-type="platform-ui">'
    '<div class="abXrZFFIQ dbXrZFFIQ gbXrZFFIQ pbXrZFFIQ cbXrZFFIQ" data-component-type="platform-ui" style="--gaps: 20px;">'
    + _ACCOUNT_CARD_MANUAL_INNER_HTML
    + "</div></div>"
)


def _action_buttons_row_inner_html() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_action_buttons_row_inner.html")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _action_buttons_disallow_only_inner_html() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_action_buttons_disallow_only_inner.html")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _build_script() -> str:
    manual_json = json.dumps(_manual_ops_payload(), ensure_ascii=False)
    detail_ops_json = json.dumps(_detail_ops_by_id_payload(), ensure_ascii=False)
    presets_json = json.dumps(_preset_payload(), ensure_ascii=False)
    balance_value = _effective_balance_for_display()
    whole, frac = f"{balance_value:,.2f}".replace(",", "X").replace(".", ",").replace("X", " ").split(",")
    balance_text = f"{whole},{frac}\u00a0₽"
    manual_account_card_inner = json.dumps(_ACCOUNT_CARD_MANUAL_INNER_HTML, ensure_ascii=False)
    manual_account_cards_shell = json.dumps(_ACCOUNT_CARDS_MANUAL_SHELL_HTML, ensure_ascii=False)
    manual_bank_details_inner = json.dumps(_BANK_DETAILS_MANUAL_INNER_HTML, ensure_ascii=False)
    manual_actions_row_inner = json.dumps(_action_buttons_row_inner_html(), ensure_ascii=False)
    manual_actions_disallow_only = json.dumps(_action_buttons_disallow_only_inner_html(), ensure_ascii=False)
    panel_origin_js = json.dumps(_panel_fetch_origin(), ensure_ascii=False)
    try:
        _restrict = not history.panel_include_all_cached_operations()
        _di, _de, _, _ = history.calculate_stats(restrict_month=_restrict)
        panel_totals_json = json.dumps(
            {"income": float(_di), "expense": float(_de)}, ensure_ascii=False
        )
    except Exception:
        panel_totals_json = '{"income":0,"expense":0}'
    _fin_dom = bool((controller.config or {}).get("browser_finanalytics_dom_patch"))
    fin_dom_js = "true" if _fin_dom else "false"
    return f"""
<script>
(function () {{
  if (window.__manualOpsBrowserInjector) return;
  window.__manualOpsBrowserInjector = true;

  const ENABLE_BROWSER_FIN_DOM_PATCH = {fin_dom_js};

  const MANUAL_OPS = {manual_json};
  const DETAIL_OPS_BY_ID = {detail_ops_json};
  const PRESETS = {presets_json};
  const BALANCE_TEXT = {json.dumps(balance_text, ensure_ascii=False)};
  const MANUAL_ACCOUNT_CARD_INNER_HTML = {manual_account_card_inner};
  const MANUAL_ACCOUNT_CARDS_SHELL_HTML = {manual_account_cards_shell};
  const MANUAL_BANK_DETAILS_INNER_HTML = {manual_bank_details_inner};
  const MANUAL_ACTIONS_ROW_INNER_HTML = {manual_actions_row_inner};
  const MANUAL_ACTIONS_DISALLOW_ONLY_INNER_HTML = {manual_actions_disallow_only};
  const PANEL_ORIGIN = {panel_origin_js};
  const PANEL_EFFECTIVE_BALANCE_URL = PANEL_ORIGIN + '/api/effective_balance';
  const PANEL_INCOME_EXPENSE_URL = PANEL_ORIGIN + '/api/panel_income_expense';
  const PANEL_OPERATIONS_URL = PANEL_ORIGIN + '/api/operations';
  const PANEL_TOTALS_SNAPSHOT = {panel_totals_json};
  let __blackBalanceLastFetch = 0;
  let __blackBalanceInFlight = false;
  let __finCardLastFetch = 0;
  let __finCardInFlight = false;
  let __homeFinMoLock = 0;
  let __homeFinPatchBusy = false;
  window.__HOME_FIN_SEEDED_FROM_API = false;

  function _panelUrlVariants(baseUrl) {{
    const u = String(baseUrl || '');
    const a = u.replace(':8082', ':8083');
    const b = u.replace(':8083', ':8082');
    const urls = [u];
    if (a !== u) urls.push(a);
    if (b !== u && b !== a) urls.push(b);
    return urls.filter(function (x, i, arr) {{ return arr.indexOf(x) === i; }});
  }}

  function fetchJsonFirstOk(urls) {{
    const list = (urls || []).filter(Boolean);
    if (!list.length) return Promise.reject(new Error('all failed'));
    if (typeof Promise.any === 'function') {{
      return Promise.any(
        list.map(function (url) {{
          return fetch(url, {{ cache: 'no-store', credentials: 'omit', mode: 'cors' }}).then(function (r) {{
            if (!r.ok) throw new Error('bad status');
            return r.json();
          }});
        }})
      );
    }}
    return new Promise(function (resolve, reject) {{
      let settled = false;
      let failed = 0;
      list.forEach(function (url) {{
        fetch(url, {{ cache: 'no-store', credentials: 'omit', mode: 'cors' }})
          .then(function (r) {{
            if (!r.ok) throw new Error('bad status');
            return r.json();
          }})
          .then(function (data) {{
            if (!settled) {{
              settled = true;
              resolve(data);
            }}
          }})
          .catch(function () {{
            failed += 1;
            if (!settled && failed >= list.length) reject(new Error('all failed'));
          }});
      }});
    }});
  }}

  function formatBalanceRubRu(value) {{
    const n = Number(value);
    if (!isFinite(n)) return '';
    const parts = n.toFixed(2).split('.');
    const whole = parts[0].replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, ' ');
    return whole + ',' + parts[1] + '\\u00a0₽';
  }}

  function formatFinanalyticsRubRu(value) {{
    const n = Number(value);
    if (!isFinite(n)) return '';
    const kops = Math.round(n * 100);
    const rub = Math.floor(Math.abs(kops) / 100);
    const kop = Math.abs(kops) % 100;
    const whole = String(rub).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '\\u00a0');
    const sign = kops < 0 ? '−' : '';
    if (kop === 0) return sign + whole + '\\u00a0₽';
    const frac = (kop < 10 ? '0' : '') + String(kop);
    return sign + whole + ',' + frac + '\\u00a0₽';
  }}

  /* Главная /mybank/: как в панели, но без копеек (целые рубли). */
  function formatFinanalyticsRubRuWhole(value) {{
    const n = Number(value);
    if (!isFinite(n)) return '';
    const rub = Math.round(Math.abs(n));
    const whole = String(rub).replace(/\\B(?=(\\d{{3}})+(?!\\d))/g, '\\u00a0');
    const sign = n < 0 ? '−' : '';
    return sign + whole + '\\u00a0₽';
  }}

  function blackBalanceSearchRoots() {{
    const out = [];
    const seen = new Set();
    function add(el) {{
      if (!el || seen.has(el)) return;
      seen.add(el);
      out.push(el);
    }}
    document.querySelectorAll('[data-panel-manual-black-card="1"]').forEach(add);
    const manualCard = document.querySelector('[data-guid="manual-operation-card"]');
    if (manualCard) add(manualCard);
    document.querySelectorAll('[data-manual-pumba-operation="1"]').forEach(function (h) {{
      const r = h.querySelector('[data-qa-type="mobile-pumba-account-operation"]');
      if (r) add(r);
    }});
    return out;
  }}

  function findAccountCellCounterpartyNameNode(accountCell) {{
    if (!accountCell) return null;
    function skipCert(el) {{
      return el && el.closest && el.closest('[data-qa-type="molecule-account-operation-cert-btn"]');
    }}
    const leg =
      accountCell.querySelector('.gbYDLs9QJ .ebYDLs9QJ span')
      || accountCell.querySelector('.gbYDLs9QJ .ebYDLs9QJ')
      || accountCell.querySelector('.gbvaqWFmO .ebvaqWFmO span')
      || accountCell.querySelector('.gbvaqWFmO .ebvaqWFmO')
      || accountCell.querySelector('.gbDhaGPUV .ebDhaGPUV span')
      || accountCell.querySelector('.gbDhaGPUV .ebDhaGPUV')
      || accountCell.querySelector('.gbZRoL7Y5 .ebZRoL7Y5 span')
      || accountCell.querySelector('.gbZRoL7Y5 .ebZRoL7Y5');
    if (leg && !skipCert(leg) && !leg.closest('[data-qa-type="atom-sensitive"]')) return leg;
    const balP = accountCell.querySelector('p[data-qa-type="molecule-account-operation-balance"]');
    const row = balP && balP.parentElement;
    if (!row) return null;
    const cols = row.querySelectorAll(':scope > div');
    for (let i = 0; i < cols.length; i++) {{
      const col = cols[i];
      if (col.querySelector('[data-qa-type="molecule-account-operation-balance"]')) continue;
      if (col.querySelector('[data-qa-type="molecule-account-operation-account-icon"]')) continue;
      const sp = col.querySelector(':scope > span');
      if (sp && !skipCert(sp) && !sp.closest('[data-qa-type="atom-sensitive"]')) return sp;
    }}
    return null;
  }}

  function findDetailAccountOperationTitleTextNode(titleWrap) {{
    if (!titleWrap) return null;
    const h2 = titleWrap.querySelector('h2[data-qa-type="tui/header.title"]');
    if (!h2) return null;
    const inner = h2.querySelector(':scope span span');
    if (inner) return inner;
    const one = h2.querySelector(':scope > span');
    if (one) {{
      const deep = one.querySelector('span');
      return deep || one;
    }}
    return h2;
  }}

  function applyBalanceTextToBlackAccountRows(text) {{
    if (!text) return;
    const roots = blackBalanceSearchRoots();
    if (!roots.length) return;
    roots.forEach(function (root) {{
      const cell = root.querySelector('[data-qa-type="tui/cell"]');
      if (!cell) return;
      const bal = cell.querySelector('[data-qa-type="molecule-account-operation-balance"] [data-qa-type="atom-sensitive"]');
      if (bal) bal.textContent = text;
    }});
  }}

  function syncBlackAccountBalanceFromPanel() {{
    if (!shouldPatchOperationsDetail()) return;
    const now = Date.now();
    if (now - __blackBalanceLastFetch < 420 || __blackBalanceInFlight) return;
    __blackBalanceLastFetch = now;
    __blackBalanceInFlight = true;
    fetchJsonFirstOk(_panelUrlVariants(PANEL_EFFECTIVE_BALANCE_URL))
      .then((data) => {{
        const v = data && data.value;
        if (v == null || !isFinite(Number(v))) return;
        applyBalanceTextToBlackAccountRows(formatBalanceRubRu(v));
      }})
      .catch(function () {{}})
      .finally(function () {{ __blackBalanceInFlight = false; }});
  }}

  function shouldSyncFinanalyticsCards() {{
    const p = location.pathname || '';
    return p.indexOf('/mybank') !== -1;
  }}

  /* Главная https://www.tbank.ru/mybank/ — блок «Траты» на десктопе без mobile-* разметки; те же суммы, что на /mybank/operations/ */
  function isMybankRootPath() {{
    const p = location.pathname || '';
    return p === '/mybank' || p === '/mybank/';
  }}

  function isMybankAccountProductPage() {{
    const p = location.pathname || '';
    if (p.indexOf('/mybank/') === -1) return false;
    if (p.indexOf('/mybank/accounts/debit/') !== -1) return true;
    if (p.indexOf('/mybank/accounts/credit/') !== -1) return true;
    if ((new RegExp('^/mybank/accounts/')).test(p)) return true;
    if (p.indexOf('/mybank/cards/') !== -1) return true;
    if (
      document.querySelector('[data-qa-type="mobile-pumba-requisites-operation"]')
      && p.indexOf('/mybank/operations') === -1
      && !document.querySelector('[data-qa-type="mobile-pumba-detail-sheet"]')
      && !document.querySelector('[data-qa-type="independent-pumba-operation-details-container"]')
    ) {{
      return true;
    }}
    return false;
  }}

  function hasNativeAccountDetailsTail() {{
    if (document.querySelector('[data-qa-type="mobile-luca-account-settings"]')) {{
      return true;
    }}
    const markers = document.querySelectorAll(
      '[data-qa-type="atom-panel-title-text"], h2[data-qa-type="tui/header.title"], [data-qa-type="tui/header.title"]'
    );
    for (let i = 0; i < markers.length; i++) {{
      const t = String(markers[i].textContent || '').replace(/\\s+/g, ' ').trim();
      if (t === 'Детали счета' || t === 'Детали счёта') return true;
      if (t.indexOf('Детали счета') !== -1 && t.length < 48) return true;
      if (t.indexOf('Детали счёта') !== -1 && t.length < 48) return true;
    }}
    return false;
  }}

  function findAccountTailAppendParent() {{
    const ib =
      document.querySelector('[data-qa-type="mobile-ib-container"]')
      || document.querySelector('main[data-qa-type="mobile-ib-container"]');
    const req =
      document.querySelector('[data-qa-type="mobile-pumba-requisites-operation"]')
      || document.querySelector('[data-qa-type="mobile-luca-black-account-requisites"]');
    const ph = document.querySelector('[data-qa-type="mobile-pumba-payment-history"]');
    const anchor = req || ph;
    if (!anchor) {{
      return ib || document.querySelector('main') || document.body;
    }}
    let el = anchor.parentElement;
    let bestCol = null;
    for (let i = 0; i < 26 && el; i++) {{
      if (el === document.body || el === document.documentElement) break;
      const st = window.getComputedStyle(el);
      const fd = String(st.flexDirection || '');
      if (st.display === 'flex' && fd.indexOf('column') !== -1) {{
        bestCol = el;
      }}
      el = el.parentElement;
    }}
    if (bestCol) {{
      if (ib && ib.contains(bestCol)) return bestCol;
      if (!ib) return bestCol;
    }}
    const main = document.querySelector('main');
    return ib || main || document.body;
  }}

  /* Включать с browser_finanalytics_dom_patch в config.json; главная /mybank патчится всегда (см. applyFinanalyticsFromTotals). */
  function shouldPatchFinanalyticsDom() {{
    if (!ENABLE_BROWSER_FIN_DOM_PATCH) return false;
    if (!shouldSyncFinanalyticsCards()) return false;
    const ua = navigator.userAgent || '';
    if (/iPhone|iPad|iPod|Android|Mobile/i.test(ua)) return true;
    if (document.querySelector('[data-qa-type="mobile-pumba-payment-history"]')) return true;
    return false;
  }}

  function isManualLikeDetailOp(op) {{
    if (!op) return false;
    const id = op.id != null ? String(op.id) : '';
    if (!id) return false;
    if (op.manual === true || op.fake_transfer === true) return true;
    for (let i = 0; i < MANUAL_OPS.length; i++) {{
      const o = MANUAL_OPS[i];
      if (o && String(o.id) === id) return true;
    }}
    return false;
  }}

  function removeManualDetailArtifacts() {{
    document.querySelectorAll('[data-manual-injected-account-cards="1"]').forEach(function (n) {{ n.remove(); }});
    document.querySelectorAll('[data-manual-pumba-operation="1"]').forEach(function (n) {{ n.remove(); }});
    document.querySelectorAll('[data-manual-bank-wrapper="1"]').forEach(function (n) {{ n.remove(); }});
    document.querySelectorAll('[data-panel-manual-black-card="1"]').forEach(function (n) {{
      n.removeAttribute('data-panel-manual-black-card');
    }});
  }}

  function getOperationDetailsContainer() {{
    return (
      document.querySelector('[data-qa-type="independent-pumba-operation-details-container"]')
      || document.querySelector('[data-qa-type="mobile-pumba-detail-sheet"]')
      || null
    );
  }}

  function ensureFinCardAmountStructure(amountWrap, formattedRub, emptyText) {{
    if (!amountWrap) return;
    amountWrap.style.overflow = 'hidden';
    amountWrap.style.whiteSpace = 'nowrap';
    let sens = amountWrap.querySelector('[data-qa-type="atom-sensitive"]');
    if (formattedRub) {{
      if (!sens) {{
        sens = document.createElement('span');
        sens.setAttribute('data-sensitive', 'true');
        sens.setAttribute('data-component-type', 'tui-react');
        sens.setAttribute('data-qa-type', 'atom-sensitive');
        sens.className = 'abIXTjPKf';
        sens.style.zIndex = '2';
        sens.style.setProperty('--tui-sensitive-offset', '30%');
        sens.style.setProperty('--tui-sensitive-mask-height', '19px');
        amountWrap.innerHTML = '';
        amountWrap.appendChild(sens);
      }}
      sens.textContent = formattedRub;
      amountWrap.setAttribute('data-manual-panel-sync', '1');
    }} else {{
      amountWrap.innerHTML = '';
      amountWrap.textContent = emptyText;
      amountWrap.setAttribute('data-manual-panel-sync', '1');
    }}
  }}

  function setFinCardSubtitle(card, label) {{
    if (!card || !label) return;
    let sub = card.querySelector('[data-qa-type="chart-card-subtitle"]');
    if (!sub) {{
      const amountWrap =
        card.querySelector('span.zb2VquEcV')
        || card.querySelector('[class*="zb2VquEcV"]');
      const row = amountWrap && amountWrap.parentElement;
      const host =
        (row && (row.querySelector('span[class*="Cb2VquEcV"]') || row.querySelector('span[class*="kbUPLfutr"]')))
        || card.querySelector('span[class*="Cb2VquEcV"]');
      if (host) {{
        sub = document.createElement('span');
        sub.setAttribute('data-qa-type', 'chart-card-subtitle');
        sub.className = 'abSmFy6N9';
        host.appendChild(sub);
      }}
    }}
    if (sub) sub.textContent = label;
  }}

  const FIN_EARNING_STRIPE_GRADIENT =
    'linear-gradient(90deg,' +
    'rgb(79,197,223) 0%, rgb(79,197,223) 72%,' +
    'rgb(255,110,20) 72%, rgb(255,110,20) 76%,' +
    'rgb(255,248,190) 76%, rgb(255,248,190) 82%,' +
    'rgb(45,200,95) 82%, rgb(45,200,95) 87%,' +
    'rgb(255,85,175) 87%, rgb(255,85,175) 93%,' +
    'rgb(220,255,228) 93%, rgb(220,255,228) 96%,' +
    'rgb(55,125,255) 96%, rgb(55,125,255) 100%)';

  const FIN_SPENDING_STRIPE_GRADIENT =
    'linear-gradient(90deg,' +
    'rgb(79,197,223) 0%, rgb(79,197,223) 73%,' +
    'rgb(210,255,218) 73%, rgb(210,255,218) 79%,' +
    'rgb(255,75,160) 79%, rgb(255,75,160) 86%,' +
    'rgb(255,252,205) 86%, rgb(255,252,205) 90%,' +
    'rgb(40,195,85) 90%, rgb(40,195,85) 95%,' +
    'rgb(65,105,255) 95%, rgb(65,105,255) 100%)';

  /* Как на витрине: голубой ~88%, фиолетовый ~8%, тёмно-синий хвост ~4%. */
  const PUMBA_HOME_ACCOUNT_STRIPE =
    'linear-gradient(90deg,' +
    'rgb(79,197,223) 0%, rgb(79,197,223) 88%,' +
    'rgb(94,99,242) 88%, rgb(94,99,242) 96%,' +
    'rgb(77,112,226) 96%, rgb(77,112,226) 100%)';

  function removeInjectedFinChartFiller(chartRoot) {{
    if (!chartRoot) return;
    const injected = chartRoot.querySelectorAll('[data-injected-fin-filler="1"]');
    injected.forEach((el) => el.remove());
  }}

  function applyFinChartStripeGradientToFilled(filled, gradientCss) {{
    if (!filled) return;
    filled.style.transform = 'translateX(0%)';
    filled.style.backgroundImage = gradientCss;
    filled.style.backgroundSize = '100% 100%';
    filled.style.color = 'transparent';
    const innerBar = filled.querySelector('[data-qa-type="chart-card-line-chart.bar"]');
    if (innerBar) innerBar.style.opacity = '0.02';
  }}

  function clearFinChartStripeGradientFromFilled(filled) {{
    if (!filled) return;
    filled.style.backgroundImage = '';
    filled.style.backgroundSize = '';
    filled.style.color = '';
    filled.style.transform = '';
    const innerBar = filled.querySelector('[data-qa-type="chart-card-line-chart.bar"]');
    if (innerBar) innerBar.style.opacity = '';
  }}

  function ensureFinChartFillerDom(track, gradientCss) {{
    let fillerWrap =
      track.querySelector('[data-qa-type*="chart-card-line-chart.filler"]')
      || track.querySelector('[class*="fbuTmnGFd"]');
    if (fillerWrap) return fillerWrap;
    fillerWrap = document.createElement('div');
    fillerWrap.setAttribute('data-qa-type', 'chart-card-line-chart.filler chart-card-line-chart.filler-0');
    fillerWrap.setAttribute('data-injected-fin-filler', '1');
    fillerWrap.className = 'fbuTmnGFd';
    fillerWrap.style.transform = 'translateX(0%)';
    const filled = document.createElement('div');
    filled.className = 'bbuTmnGFd cbuTmnGFd';
    filled.setAttribute('data-injected-fin-filler', '1');
    filled.style.transform = 'translateX(0%)';
    const bar = document.createElement('div');
    bar.setAttribute('data-qa-type', 'chart-card-line-chart.bar');
    bar.setAttribute('data-injected-fin-filler', '1');
    bar.className = 'dbuTmnGFd';
    filled.appendChild(bar);
    applyFinChartStripeGradientToFilled(filled, gradientCss);
    fillerWrap.appendChild(filled);
    track.appendChild(fillerWrap);
    return fillerWrap;
  }}

  function setFinCardChartStripe(card, hasAmount, isIncome) {{
    const chartRoot = card.querySelector('[data-qa-type="chart-card-line-chart"]');
    if (!chartRoot) return;
    const gradientCss = isIncome ? FIN_EARNING_STRIPE_GRADIENT : FIN_SPENDING_STRIPE_GRADIENT;
    const track = chartRoot.querySelector('.ebuTmnGFd') || chartRoot.querySelector('[class*="ebuTmnGFd"]');
    const neutral = chartRoot.querySelector('.bbuTmnGFd:not(.cbuTmnGFd)');
    if (hasAmount) {{
      card.setAttribute('data-manual-fin-chart', '1');
      if (neutral) neutral.style.opacity = '0.35';
      let fillerWrap =
        chartRoot.querySelector('[data-qa-type*="chart-card-line-chart.filler"]')
        || chartRoot.querySelector('[class*="fbuTmnGFd"]');
      if (track && !fillerWrap) fillerWrap = ensureFinChartFillerDom(track, gradientCss);
      const filled = chartRoot.querySelector('.bbuTmnGFd.cbuTmnGFd');
      if (fillerWrap) {{
        fillerWrap.style.transform = 'translateX(0%)';
        fillerWrap.style.opacity = '1';
      }}
      if (filled) {{
        applyFinChartStripeGradientToFilled(filled, gradientCss);
      }}
    }} else {{
      card.removeAttribute('data-manual-fin-chart');
      removeInjectedFinChartFiller(chartRoot);
      if (neutral) neutral.style.opacity = '';
      const fillerWrap =
        chartRoot.querySelector('[data-qa-type*="chart-card-line-chart.filler"]')
        || chartRoot.querySelector('[class*="fbuTmnGFd"]');
      const filled = chartRoot.querySelector('.bbuTmnGFd.cbuTmnGFd');
      if (fillerWrap) {{
        fillerWrap.style.transform = '';
        fillerWrap.style.opacity = '';
      }}
      if (filled) {{
        clearFinChartStripeGradientFromFilled(filled);
      }}
    }}
  }}

  function findFinCardAmountWrap(card) {{
    if (!card) return null;
    let w = card.querySelector('span.zb2VquEcV');
    if (w) return w;
    w = card.querySelector('[class*="zb2VquEcV"]');
    if (w) return w;
    const sub = card.querySelector('[data-qa-type="chart-card-subtitle"]');
    const row = sub && sub.parentElement;
    if (row) {{
      const spans = row.querySelectorAll('span');
      for (let i = 0; i < spans.length; i++) {{
        const el = spans[i];
        const cls = String(el.className || '');
        if (cls.indexOf('zb2VquEcV') !== -1) return el;
      }}
    }}
    return null;
  }}

  function collectFinCardsBySubtitle(keyword) {{
    const kw = String(keyword || '').toLowerCase();
    const out = [];
    document.querySelectorAll('[data-qa-type="click-area"]').forEach(function (c) {{
      const sub = c.querySelector('[data-qa-type="chart-card-subtitle"]');
      const t = String(sub && sub.textContent || '').toLowerCase().replace(/\\s+/g, ' ');
      if (kw && t.indexOf(kw) !== -1) out.push(c);
    }});
    return out;
  }}

  function collectSpendingFinCards() {{
    let spendCards = document.querySelectorAll('[data-qa-type="click-area spending-card"]');
    if (spendCards.length) return Array.from(spendCards);
    const byRashod = collectFinCardsBySubtitle('расход');
    const byTraty = collectFinCardsBySubtitle('трат');
    const seen = new Set(byRashod);
    const out = byRashod.slice();
    byTraty.forEach(function (c) {{
      if (!seen.has(c)) {{
        seen.add(c);
        out.push(c);
      }}
    }});
    return out;
  }}

  function patchFinanalyticsCard(card, val, emptyText, subtitleLabel, isIncome, wholeRubHome) {{
    if (!card) return;
    const amountWrap = findFinCardAmountWrap(card);
    if (!amountWrap) return;
    if (val > 0) {{
      const rubTxt = wholeRubHome ? formatFinanalyticsRubRuWhole(val) : formatFinanalyticsRubRu(val);
      ensureFinCardAmountStructure(amountWrap, rubTxt, emptyText);
    }} else {{
      ensureFinCardAmountStructure(amountWrap, '', emptyText);
    }}
    setFinCardSubtitle(card, subtitleLabel);
    setFinCardChartStripe(card, val > 0, isIncome);
  }}

  function currentMonthGenitiveRu() {{
    const m = [
      'январе', 'феврале', 'марте', 'апреле', 'мае', 'июне',
      'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре'
    ];
    return m[new Date().getMonth()] || '';
  }}

  function pumbaLineChartTrackHost(lineChart) {{
    if (!lineChart) return null;
    return (
      lineChart.querySelector('[class*="ebMee5y"]')
      || lineChart.querySelector('[class*="abMee5y"]')
      || lineChart.querySelector('[class*="Mee5y-X"]')
      || lineChart.querySelector('[class*="Mee5y"]')
    );
  }}

  function hasPumbaNativeFillers(lineChart) {{
    return !!(lineChart && lineChart.querySelector('[data-qa-type^="lineChart.filler"]'));
  }}

  function pumbaNeutralTrackBarEl(lineChart) {{
    const eb = pumbaLineChartTrackHost(lineChart);
    if (!eb) return null;
    const dbs = eb.querySelectorAll('[class*="dbMee5y"]');
    for (var i = 0; i < dbs.length; i++) {{
      const el = dbs[i];
      if (!el.closest('[data-qa-type^="lineChart.filler"]')) return el;
    }}
    return null;
  }}

  function ensurePumbaAccountPageStripeWhenNoFillers(lineChart, exp) {{
    if (!lineChart || exp <= 0) return;
    if (hasPumbaNativeFillers(lineChart)) return;
    const barEl = pumbaNeutralTrackBarEl(lineChart);
    if (!barEl) return;
    barEl.setAttribute('data-manual-payment-history-chart-host', '1');
    barEl.style.backgroundImage = PUMBA_HOME_ACCOUNT_STRIPE;
    barEl.style.backgroundSize = '100% 100%';
    barEl.style.backgroundRepeat = 'no-repeat';
    barEl.style.borderRadius = '9999px';
    barEl.style.minHeight = '8px';
    const innerBar = barEl.querySelector('[data-qa-type="lineChart.bar"]');
    if (innerBar) {{
      innerBar.setAttribute('data-manual-ph-hidden', '1');
      innerBar.style.opacity = '0';
    }}
  }}

  function ensurePaymentHistorySubtitleStyles() {{
    let st = document.getElementById('manual-payment-history-subtitle-styles');
    if (!st) {{
      st = document.createElement('style');
      st.id = 'manual-payment-history-subtitle-styles';
      (document.head || document.documentElement).appendChild(st);
    }}
    st.textContent =
      '[data-qa-type="mobile-pumba-payment-history"] [data-manual-ph-line] {{ display: block; line-height: 1.25; }}' +
      '[data-qa-type="mobile-pumba-payment-history"] [data-manual-ph-amt] {{ display: block; margin-top: 4px; line-height: 1.35; font-weight: 400; color: rgba(0,0,0,0.55); font-size: 14px; }}';
    let st2 = document.getElementById('manual-payment-history-ext-styles');
    if (!st2) {{
      st2 = document.createElement('style');
      st2.id = 'manual-payment-history-ext-styles';
      (document.head || document.documentElement).appendChild(st2);
    }}
    st2.textContent =
      '[data-manual-debit-account-ph="1"] [data-qa-type="click-area"][data-appearance="elevated"],' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="click-area"][data-surface="true"],' +
      '[data-manual-debit-account-ph="1"] > [data-qa-type="click-area"] {{ border-radius: 20px; box-sizing: border-box; box-shadow: var(--tui-shadow-small, 0px 5px 20px 0px #0000001A); }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="title"] {{ font: var(--pumba-payment-history-title-font, var(--tui-font-heading-mobile-s-bold, 700 16px/1.19 var(--tui-font-text, Roboto), system-ui, sans-serif)); color: var(--tui-text-primary, #333); white-space: var(--pumba-payment-history-title-white-space, normal); display: block; margin: 0 0 8px; }}' +
      '[data-manual-debit-account-ph="1"] h2[data-qa-type="tui/header.title"] {{ font: var(--pumba-payment-history-title-font, var(--tui-font-heading-mobile-s-bold)); color: var(--tui-text-primary, #333); margin: 0 0 8px; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="subtitleWrapper"] {{ display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="subtitleWrapper"] [data-qa-type="subtitle"],' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="subtitle"] {{ font: var(--pumba-payment-history-subtitle-font, var(--tui-font-text-mobile-m, 400 15px/1.43 var(--tui-font-text, Roboto), system-ui, sans-serif)); color: var(--tds-color-text-01, rgba(0,0,0,0.8)) !important; -webkit-text-fill-color: var(--tds-color-text-01, rgba(0,0,0,0.8)); margin: 0; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="moneyAmount"],' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="moneyAmount"] [data-qa-type="atom-sensitive"],' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="moneyAmount"] [data-qa-type="uikit/money"],' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="moneyAmount"] [data-qa-type="uikit/money"] span {{ font: var(--tui-font-text-mobile-m-bold, 600 15px/1.43 var(--tui-font-text, Roboto), system-ui, sans-serif); color: var(--tds-color-text-01, #000000) !important; -webkit-text-fill-color: var(--tds-color-text-01, #000000); margin: 0; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="lineChart"] {{ margin-top: var(--pumba-payment-history-progressLine-padding-top, 12px); width: 100%; isolation: isolate; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="lineChart"] [data-qa-type^="lineChart.filler"] {{ opacity: 1 !important; visibility: visible !important; }}' +
      '[data-manual-debit-account-ph="1"] [data-qa-type="lineChart"] [class*="Mee5y"] [data-qa-type="lineChart.bar"] {{ opacity: 1 !important; border-radius: 9999px; }}' +
      '[data-manual-debit-account-ph="1"] [data-manual-ph-line] {{ color: rgba(0,0,0,0.78) !important; }}' +
      '[data-manual-debit-account-ph="1"] [data-manual-ph-amt] {{ color: rgba(0,0,0,0.92) !important; font-weight: 600; }}';
    let st4 = document.getElementById('manual-luca-account-blocks-styles');
    if (!st4) {{
      st4 = document.createElement('style');
      st4.id = 'manual-luca-account-blocks-styles';
      (document.head || document.documentElement).appendChild(st4);
    }}
    st4.textContent =
      '[data-manual-qr-link-row="1"] {{ display: flex; flex-direction: row; gap: 12px; margin-top: 12px; margin-bottom: 0; width: 100%; box-sizing: border-box; }}' +
      '[data-manual-qr-link-row="1"] .manual-qr-link-cell {{ flex: 1; min-width: 0; background: var(--tui-background-elevation-1, #fff); border-radius: 16px; box-shadow: var(--tui-shadow-small, 0 5px 20px rgba(0,0,0,0.1)); padding: 16px 14px 14px; position: relative; box-sizing: border-box; }}' +
      '[data-manual-qr-link-row="1"] .manual-qr-link-title {{ font: 700 17px/1.2 var(--tui-font-text, Roboto), system-ui, sans-serif; color: var(--tui-text-primary, #333); margin: 0 0 4px; padding-right: 28px; }}' +
      '[data-manual-qr-link-row="1"] .manual-qr-link-desc {{ font: 400 15px/1.43 var(--tui-font-text, Roboto), system-ui, sans-serif; color: var(--tui-text-secondary, #9299a2); margin: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}' +
      '[data-manual-qr-link-row="1"] .manual-qr-link-icon {{ position: absolute; top: 14px; right: 12px; color: var(--tui-text-action, #428bf9); display: flex; }}' +
      '[data-manual-debit-tail-section="1"] {{ display: flex; flex-direction: column; gap: 12px; margin-top: 12px; width: 100%; box-sizing: border-box; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-card.manual-debit-details-card {{ background: var(--tui-background-neutral-1-on-base, var(--tui-background-base-alt, #f6f7f8)); border-radius: 20px; border: none; box-shadow: none; overflow: hidden; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-header {{ padding: 16px 16px 4px; font: 700 18px/1.2 var(--tui-typography-font-family-text, Roboto), system-ui, sans-serif; color: var(--tui-text-primary, #333333); }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; margin: 0; border: none; font: 400 16px/1.43 var(--tui-typography-font-family-text, Roboto), system-ui, sans-serif; color: var(--tui-text-primary, #333333); cursor: default; box-sizing: border-box; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-row:last-child {{ padding-bottom: 16px; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-row .manual-debit-tail-row-text {{ flex: 1; min-width: 0; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-row[data-manual-statements-nav="1"] {{ cursor: pointer; -webkit-tap-highlight-color: transparent; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-chevron {{ flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; color: var(--tui-text-tertiary, rgba(0,16,36,0.22)); width: 18px; height: 18px; }}' +
      '[data-manual-debit-tail-section="1"] .manual-debit-tail-chevron svg {{ display: block; }}';
  }}

  function ensurePumbaLineChartLikeHome(lineChart) {{
    if (!lineChart) return;
    lineChart.setAttribute('data-size', 'sm');
    lineChart.setAttribute('data-linecap', 'square');
    if (!lineChart.getAttribute('data-component-type')) {{
      lineChart.setAttribute('data-component-type', 'platform-ui');
    }}
  }}

  function schedulePumbaLineChartHomeShape(lineChart) {{
    if (!lineChart) return;
    requestAnimationFrame(function () {{
      ensurePumbaLineChartLikeHome(lineChart);
    }});
  }}

  function clearManualPumbaLineChart(lineChart) {{
    if (!lineChart) return;
    const hasNativeFillers = !!lineChart.querySelector('[data-qa-type^="lineChart.filler"]');
    const manualHost = lineChart.querySelector('[data-manual-payment-history-chart-host="1"]');
    if (manualHost) {{
      manualHost.removeAttribute('data-manual-payment-history-chart-host');
      manualHost.style.backgroundImage = '';
      manualHost.style.backgroundSize = '';
      manualHost.style.backgroundRepeat = '';
      manualHost.style.borderRadius = '';
      manualHost.style.minHeight = '';
      manualHost.querySelectorAll('[data-manual-ph-hidden="1"]').forEach(function (ch) {{
        ch.removeAttribute('data-manual-ph-hidden');
        ch.style.opacity = '';
      }});
    }}
    /* Нативный Mee5y: не трогать host.style — банк задаёт сегменты инлайном; сброс ломал цвета. */
    lineChart.removeAttribute('data-manual-payment-history-chart');
    const legacyBar =
      lineChart.querySelector('.db5ygiFRy[data-manual-payment-history-chart="1"]')
      || lineChart.querySelector('[class*="db5ygiFRy"][data-manual-payment-history-chart="1"]');
    if (legacyBar) {{
      legacyBar.style.backgroundImage = '';
      legacyBar.style.backgroundSize = '';
      legacyBar.style.backgroundRepeat = '';
      legacyBar.style.minHeight = '';
      legacyBar.style.borderRadius = '';
      legacyBar.removeAttribute('data-manual-payment-history-chart');
    }}
    const legacyTrack =
      lineChart.querySelector('.bb5ygiFRy')
      || lineChart.querySelector('[class*="bb5ygiFRy"]');
    if (legacyTrack) legacyTrack.style.opacity = '';
    lineChart.querySelectorAll('[data-manual-ph-hidden="1"]').forEach(function (ch) {{
      ch.removeAttribute('data-manual-ph-hidden');
      ch.style.opacity = '';
    }});
  }}

  function applyManualPumbaLineChartSpending(lineChart) {{
    if (!lineChart) return;
    if (lineChart.querySelector('[data-qa-type^="lineChart."]')) {{
      clearManualPumbaLineChart(lineChart);
      return;
    }}
    const hostNew = pumbaLineChartTrackHost(lineChart);
    const hasNewPumbaChart =
      !!hostNew || !!lineChart.querySelector('[data-qa-type^="lineChart.filler"]');
    const host = hostNew || (hasNewPumbaChart ? lineChart.firstElementChild : null);
    const cls = String(host && host.className || '');
    if (host && hasNewPumbaChart && (cls.indexOf('Mee5y') !== -1 || lineChart.querySelector('[data-qa-type^="lineChart.filler"]'))) {{
      clearManualPumbaLineChart(lineChart);
      return;
    }}
    const bar =
      lineChart.querySelector('.db5ygiFRy')
      || lineChart.querySelector('[class*="db5ygiFRy"]');
    const track =
      lineChart.querySelector('.bb5ygiFRy')
      || lineChart.querySelector('[class*="bb5ygiFRy"]');
    if (bar) {{
      bar.setAttribute('data-manual-payment-history-chart', '1');
      bar.style.backgroundImage = FIN_SPENDING_STRIPE_GRADIENT;
      bar.style.backgroundSize = '100% 100%';
      bar.style.backgroundRepeat = 'no-repeat';
      bar.style.minHeight = '6px';
      bar.style.borderRadius = '2px';
    }}
    if (track) track.style.opacity = '0.92';
  }}

  function setPumbaPaymentMoneyAmount(moneyEl, formattedRub) {{
    if (!moneyEl) return;
    moneyEl.setAttribute('data-manual-panel-sync', '1');
    const money = moneyEl.querySelector('[data-qa-type="uikit/money"]');
    if (money) {{
      money.textContent = formattedRub;
      return;
    }}
    const sens = moneyEl.querySelector('[data-qa-type="atom-sensitive"]');
    if (sens) {{
      sens.textContent = formattedRub;
      return;
    }}
    moneyEl.textContent = formattedRub;
  }}

  function currentDebitAccountIdFromPath() {{
    const m = /\\/mybank\\/accounts\\/debit\\/(\\d+)/.exec(location.pathname || '');
    return m ? m[1] : '';
  }}

  function currentAccountIdFromPath() {{
    const m = /\\/mybank\\/accounts\\/[^/]+\\/(\\d+)/.exec(location.pathname || '');
    return m ? m[1] : '';
  }}

  function normalizeUiText(t) {{
    return String(t || '')
      .replace(/\\r|\\n/g, ' ')
      .replace(/\\s+/g, ' ')
      .trim();
  }}

  function statementsListRelPath(accountId) {{
    return '/mybank/statements/?accountId=' + encodeURIComponent(accountId);
  }}

  function statementsListUrlForAccount(accountId) {{
    if (!accountId) return '';
    const o = (typeof location !== 'undefined' && location.origin) ? String(location.origin).replace(/\\/$/, '') : '';
    return (o || '') + statementsListRelPath(accountId);
  }}

  function tryTramvaiRouterNavigate(relPath) {{
    const o = (typeof location !== 'undefined' && location.origin) ? String(location.origin).replace(/\\/$/, '') : '';
    const full = (o || '') + relPath;
    const apps = [window.__TRAMVAI_APP__, window.__TRAMVAI__, window.tramvai];
    for (let i = 0; i < apps.length; i++) {{
      const app = apps[i];
      if (!app) continue;
      let r = app.router;
      if (!r && app.di && typeof app.di.get === 'function') {{
        try {{
          r = app.di.get('router');
        }} catch (eDi) {{
          r = null;
        }}
      }}
      if (!r || typeof r.navigate !== 'function') continue;
      try {{
        r.navigate({{ url: full }});
        return true;
      }} catch (e1) {{}}
      try {{
        r.navigate({{ url: relPath }});
        return true;
      }} catch (e2) {{}}
      try {{
        r.navigate(relPath);
        return true;
      }} catch (e3) {{}}
      try {{
        const q = relPath.indexOf('?');
        const pn = q >= 0 ? relPath.slice(0, q) : relPath;
        const sc = q >= 0 ? relPath.slice(q) : '';
        r.navigate({{ pathname: pn, search: sc }});
        return true;
      }} catch (e4) {{}}
    }}
    return false;
  }}

  function navigateToStatementsOrderPage(accountId) {{
    if (!accountId) return;
    const rel = statementsListRelPath(accountId);
    if (tryTramvaiRouterNavigate(rel)) return;
    requestAnimationFrame(function () {{
      if (tryTramvaiRouterNavigate(rel)) return;
      window.location.assign(statementsListUrlForAccount(accountId));
    }});
  }}

  function bindOrderCertificateStatementsClick() {{
    if (window.__manualOrderCertificateStatementsBound) return;
    window.__manualOrderCertificateStatementsBound = true;
    document.addEventListener(
      'click',
      function (ev) {{
        const accountId = currentAccountIdFromPath();
        if (!accountId) return;
        if (!isMybankAccountProductPage()) return;
        const t = ev.target;
        if (!t || !t.closest) return;
        if (t.closest('[data-manual-statements-nav="1"]')) {{
          ev.preventDefault();
          ev.stopPropagation();
          navigateToStatementsOrderPage(accountId);
          return;
        }}
        const settings = document.querySelector('[data-qa-type="mobile-luca-account-settings"]');
        if (!settings || !settings.contains(t)) return;
        if (t.closest('a[href*="/mybank/statements"]')) return;
        let el = t;
        let hit = null;
        for (let i = 0; i < 28 && el; i++) {{
          if (!settings.contains(el)) break;
          if (normalizeUiText(el.textContent) === 'Заказать справку') {{
            hit = el;
            break;
          }}
          el = el.parentElement;
        }}
        if (!hit) return;
        const rowA = hit.querySelector && hit.querySelector('a[href*="/mybank/statements"]');
        if (rowA) return;
        ev.preventDefault();
        ev.stopPropagation();
        navigateToStatementsOrderPage(accountId);
      }},
      true
    );
  }}

  function manualRowChevronHtml() {{
    return '<span class="manual-debit-tail-chevron" aria-hidden="true"><svg width="18" height="18" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" focusable="false"><path fill-rule="evenodd" clip-rule="evenodd" d="M12.5 8 6.658 2 5.316 3.423 9.816 8l-4.5 4.577L6.658 14 12.5 8Z" fill="currentColor"></path></svg></span>';
  }}

  function findManualDetailsInsertAnchor() {{
    const manualQr = document.querySelector('[data-manual-qr-link-row="1"]');
    if (manualQr) return manualQr;
    const link = document.querySelector('[data-qa-type="mobile-luca-account-requisites-link"]');
    if (link) {{
      const row = link.closest('[data-component-type="blocks-ib"]');
      if (row) return row;
    }}
    const qr = document.querySelector('[data-qa-type="mobile-luca-account-requisites-qr"]');
    if (qr) {{
      const row = qr.closest('[data-component-type="blocks-ib"]');
      if (row) return row;
    }}
    return null;
  }}

  function ensureManualQrLinkRowAfterRequisites() {{
    if (!isMybankAccountProductPage()) {{
      document.querySelectorAll('[data-manual-qr-link-row="1"]').forEach(function (n) {{ try {{ n.remove(); }} catch (eQ) {{}} }});
      return;
    }}
    if (document.querySelector('[data-qa-type="mobile-luca-account-requisites-qr"]')) {{
      document.querySelectorAll('[data-manual-qr-link-row="1"]').forEach(function (n) {{ try {{ n.remove(); }} catch (eQ2) {{}} }});
      return;
    }}
    const reqRoot = document.querySelector('[data-qa-type="mobile-luca-black-account-requisites"]');
    if (!reqRoot || !reqRoot.parentElement) return;
    const aid = currentDebitAccountIdFromPath();
    const linkHint = aid
      ? 'https://www.tbank.ru/mybank/payments/quick/replenishment?accountId=' + aid
      : 'https://www.tbank.ru/mybank/';
    let row = document.querySelector('[data-manual-qr-link-row="1"]');
    if (!row) {{
      row = document.createElement('div');
      row.setAttribute('data-manual-qr-link-row', '1');
      row.innerHTML =
        '<div class="manual-qr-link-cell">' +
        '<div class="manual-qr-link-icon" aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" focusable="false"><path fill-rule="evenodd" clip-rule="evenodd" d="M14 14V2H2v12h12Zm-3-3V5H5v6h6Z" fill="currentColor"></path><path d="M9.1 6.9v2.2H6.9V6.9h2.2Z" fill="currentColor"></path><path opacity=".85" fill-rule="evenodd" clip-rule="evenodd" d="M17 2h5v9h-5V8h2.5V5H17V2ZM2 22h3v-2.5h3V22h3v-5H2v5Z" fill="currentColor"></path><path fill-rule="evenodd" clip-rule="evenodd" d="M22 22v-8h-8v8h8Zm-2.8-2.8v-2.4h-2.4v2.4h2.4Z" fill="currentColor"></path></svg></div>' +
        '<div class="manual-qr-link-title">QR-код</div>' +
        '<div class="manual-qr-link-desc">Для пополнения</div>' +
        '</div>' +
        '<div class="manual-qr-link-cell">' +
        '<div class="manual-qr-link-icon" aria-hidden="true"><svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" focusable="false"><path fill-rule="evenodd" clip-rule="evenodd" d="M13.78 2.317a4.497 4.497 0 0 1 6.36 0l1.543 1.544a4.497 4.497 0 0 1 0 6.359l-3.089 3.089a2.998 2.998 0 0 1 0-4.24l.97-.969a1.499 1.499 0 0 0 0-2.12L18.02 4.437a1.499 1.499 0 0 0-2.12 0l-5.468 5.467a1.499 1.499 0 0 0 0 2.12 2.352 2.352 0 0 1 0 3.327l-.456.456-1.663-1.663a4.497 4.497 0 0 1 0-6.36l5.467-5.467Z" fill="currentColor"></path><path fill-rule="evenodd" clip-rule="evenodd" d="M10.22 21.683a4.497 4.497 0 0 1-6.36 0l-1.543-1.544a4.497 4.497 0 0 1 0-6.359l3.09-3.09a2.998 2.998 0 0 1 0 4.239l-.97.97a1.499 1.499 0 0 0 0 2.12l1.543 1.544a1.499 1.499 0 0 0 2.12 0l5.468-5.467a1.499 1.499 0 0 0 0-2.12 2.352 2.352 0 0 1 0-3.327l.456-.456 1.663 1.663a4.497 4.497 0 0 1 0 6.36l-5.467 5.467Z" fill="currentColor"></path></svg></div>' +
        '<div class="manual-qr-link-title">Ссылка</div>' +
        '<div class="manual-qr-link-desc">' + linkHint + '</div>' +
        '</div>';
      reqRoot.parentElement.insertBefore(row, reqRoot.nextSibling);
    }} else if (reqRoot.nextSibling !== row) {{
      reqRoot.parentElement.insertBefore(row, reqRoot.nextSibling);
    }}
  }}

  function placeManualDetailsSection(section) {{
    if (!section) return;
    const anchor = findManualDetailsInsertAnchor();
    const fallbackParent = findAccountTailAppendParent();
    const parent =
      (anchor && anchor.parentElement) || fallbackParent || document.querySelector('main') || document.body;
    if (!parent) return;
    if (anchor) {{
      if (section.parentElement !== parent || section.previousElementSibling !== anchor) {{
        parent.insertBefore(section, anchor.nextSibling);
      }}
    }} else {{
      parent.appendChild(section);
    }}
  }}

  function ensureDebitAccountLowerBlocks() {{
    ensurePaymentHistorySubtitleStyles();
    if (!isMybankAccountProductPage()) {{
      document.querySelectorAll('[data-manual-debit-tail-section="1"]').forEach(function (n) {{ try {{ n.remove(); }} catch (eR) {{}} }});
      document.querySelectorAll('[data-manual-qr-link-row="1"]').forEach(function (n) {{ try {{ n.remove(); }} catch (eRq) {{}} }});
      return;
    }}
    ensureManualQrLinkRowAfterRequisites();
    if (hasNativeAccountDetailsTail()) {{
      document.querySelectorAll('[data-manual-debit-tail-section="1"]').forEach(function (n) {{ try {{ n.remove(); }} catch (eR2) {{}} }});
      return;
    }}
    let section = document.querySelector('[data-manual-debit-tail-section="1"]');
    if (!section) {{
      section = document.createElement('div');
      section.setAttribute('data-manual-debit-tail-section', '1');
      const ch = manualRowChevronHtml();
      section.innerHTML =
        '<div class="manual-debit-tail-card manual-debit-details-card">' +
        '<div class="manual-debit-tail-header">Детали счета</div>' +
        '<div class="manual-debit-tail-row"><span class="manual-debit-tail-row-text">Лимиты на переводы,<br>снятия и пополнения</span>' + ch + '</div>' +
        '<div class="manual-debit-tail-row"><span class="manual-debit-tail-row-text">Тариф</span>' + ch + '</div>' +
        '<div class="manual-debit-tail-row"><span class="manual-debit-tail-row-text">Выписка по счету</span>' + ch + '</div>' +
        '<div class="manual-debit-tail-row" data-manual-statements-nav="1"><span class="manual-debit-tail-row-text">Заказать справку</span>' + ch + '</div>' +
        '<div class="manual-debit-tail-row"><span class="manual-debit-tail-row-text">Защита карт</span>' + ch + '</div>' +
        '</div>';
    }}
    placeManualDetailsSection(section);
  }}

  /* Виджет «Все операции» / «Трат в …» на главной (React перерисовывает — держим сумму с /api/operations). */
  function patchOneHomeAllOperationsScope(scope, exp) {{
    if (!scope) return;
    const n = Number(exp);
    if (!isFinite(n)) return;
    const month = currentMonthGenitiveRu();
    const titleLine = 'Трат в\\u00a0' + month;
    const amt = formatFinanalyticsRubRuWhole(n);
    scope.setAttribute('data-manual-home-allops-tile', '1');
    let moneyEl =
      scope.querySelector('[data-qa-type="subtitleWrapper"] [data-qa-type="moneyAmount"]')
      || scope.querySelector('[data-qa-type="moneyAmount"]');
    if (!moneyEl) {{
      const cand = scope.querySelectorAll('span, div, p');
      for (let j = 0; j < cand.length; j++) {{
        const el = cand[j];
        const t = normalizeUiText(el.textContent || '');
        if (t.indexOf('₽') === -1) continue;
        if (el.querySelector && el.querySelector('span') && t.split('₽').length > 2) continue;
        if (t.length > 36) continue;
        if (/трат/i.test(t) && t.length < 48) continue;
        moneyEl = el;
        break;
      }}
    }}
    if (moneyEl) setPumbaPaymentMoneyAmount(moneyEl, amt);
    const subs = scope.querySelectorAll('[data-qa-type="subtitle"], [data-qa-type="chart-card-subtitle"], p, span');
    for (let k = 0; k < subs.length; k++) {{
      const el = subs[k];
      const tx = normalizeUiText(el.textContent || '');
      if (!/трат/i.test(tx)) continue;
      if (tx.indexOf('₽') !== -1 && tx.length > 20) continue;
      el.textContent = titleLine;
      el.setAttribute('data-manual-panel-sync', '1');
      break;
    }}
    const lineChart = scope.querySelector('[data-qa-type="lineChart"]');
    if (lineChart && n > 0) {{
      if (hasPumbaNativeFillers(lineChart)) {{
        schedulePumbaLineChartHomeShape(lineChart);
      }} else if (pumbaLineChartTrackHost(lineChart)) {{
        ensurePumbaAccountPageStripeWhenNoFillers(lineChart, n);
      }} else {{
        applyManualPumbaLineChartSpending(lineChart);
      }}
    }}
  }}

  function patchHomeAllOperationsSpendingBlock(exp) {{
    if (!isMybankRootPath()) return;
    const n = Number(exp);
    if (!isFinite(n)) return;

    const mainRoot =
      document.querySelector('main[data-qa-type="mobile-ib-container"]')
      || document.querySelector('main')
      || document.body;
    const titleSel =
      '[data-qa-type="tui/header.title"], h2, h3, [data-qa-type="atom-panel-title-text"], [data-qa-type="title"], span, div';
    const seenScopes = new Set();
    mainRoot.querySelectorAll(titleSel).forEach(function (titleEl) {{
      const raw = normalizeUiText(titleEl.textContent || '');
      if (raw !== 'Все операции' && raw.indexOf('Все операции') !== 0) return;
      let scope = titleEl.closest('[data-qa-type="click-area"]');
      if (!scope) {{
        let p = titleEl.parentElement;
        for (let d = 0; d < 10 && p; d++, p = p.parentElement) {{
          if (p.querySelector && p.querySelector('[data-qa-type="moneyAmount"], [data-qa-type="lineChart"]')) {{
            scope = p;
            break;
          }}
        }}
      }}
      if (!scope) scope = titleEl.parentElement;
      if (!scope) return;
      if (seenScopes.has(scope)) return;
      seenScopes.add(scope);
      patchOneHomeAllOperationsScope(scope, exp);
    }});

    function opsListHref(href) {{
      const s = String(href || '').split('#')[0];
      if (s.indexOf('/mybank/operations') === -1) return false;
      if (/[?&](?:operation_?id|id)=/i.test(s)) return false;
      return true;
    }}

    const anchors = mainRoot.querySelectorAll('a[href*="/mybank/operations"]');
    for (let i = 0; i < anchors.length; i++) {{
      const a = anchors[i];
      const h = a.href || a.getAttribute('href') || '';
      if (!opsListHref(h)) continue;
      let scope = a.closest('[data-qa-type="click-area"]');
      if (!scope) scope = a.closest('article, section');
      if (!scope) scope = a.parentElement;
      if (!scope) continue;
      let big = scope;
      for (let up = 0; up < 6 && big && big.parentElement; up++) {{
        if (normalizeUiText(big.innerText || '').indexOf('Все операции') !== -1) break;
        big = big.parentElement;
      }}
      if (big && normalizeUiText(big.innerText || '').indexOf('Все операции') !== -1) scope = big;
      if (normalizeUiText(scope.innerText || '').indexOf('Все операции') === -1) continue;
      if (seenScopes.has(scope)) continue;
      seenScopes.add(scope);
      patchOneHomeAllOperationsScope(scope, exp);
    }}

    mainRoot.querySelectorAll('[data-qa-type="mobile-pumba-payment-history"]').forEach(function (root) {{
      if (normalizeUiText(root.innerText || '').indexOf('Все операции') === -1) return;
      if (seenScopes.has(root)) return;
      seenScopes.add(root);
      patchOneHomeAllOperationsScope(root, exp);
    }});
  }}

  function syncMobilePumbaPaymentHistory(inc, exp) {{
    ensurePaymentHistorySubtitleStyles();
    const month = currentMonthGenitiveRu();
    const debitAcct = isMybankAccountProductPage();
    document.querySelectorAll('[data-qa-type="mobile-pumba-payment-history"]').forEach(function (root) {{
      if (debitAcct) {{
        root.setAttribute('data-manual-debit-account-ph', '1');
      }} else {{
        root.removeAttribute('data-manual-debit-account-ph');
      }}
      const wrap = root.querySelector('[data-qa-type="subtitleWrapper"]');
      const sub =
        (wrap && wrap.querySelector('[data-qa-type="subtitle"]'))
        || root.querySelector('[data-qa-type="subtitle"]');
      const moneyEl =
        (wrap && wrap.querySelector('[data-qa-type="moneyAmount"]'))
        || root.querySelector('[data-qa-type="moneyAmount"]');
      if (sub) {{
        if (exp > 0) {{
          const titleLine = 'Трат в\\u00a0' + month;
          const amt = isMybankRootPath() ? formatFinanalyticsRubRuWhole(exp) : formatFinanalyticsRubRu(exp);
          if (wrap && moneyEl) {{
            sub.textContent = titleLine;
            setPumbaPaymentMoneyAmount(moneyEl, amt);
            wrap.setAttribute('data-manual-panel-sync', '1');
          }} else {{
            sub.innerHTML =
              '<span data-manual-ph-line="1">' + titleLine + '</span>' +
              '<span data-manual-ph-amt="1">' + amt + '</span>';
            sub.setAttribute('data-manual-panel-sync', '1');
          }}
        }} else {{
          if (moneyEl) {{
            const uikit = moneyEl.querySelector('[data-qa-type="uikit/money"]');
            if (uikit) uikit.textContent = '';
            else moneyEl.textContent = '';
          }}
          sub.textContent = 'Нет трат в\\u00a0' + month;
          sub.setAttribute('data-manual-panel-sync', '1');
          if (wrap) wrap.setAttribute('data-manual-panel-sync', '1');
        }}
      }}
      const lineChart = root.querySelector('[data-qa-type="lineChart"]');
      if (!lineChart || !debitAcct) return;
      clearManualPumbaLineChart(lineChart);
      if (hasPumbaNativeFillers(lineChart)) {{
        schedulePumbaLineChartHomeShape(lineChart);
      }} else if (exp > 0) {{
        if (pumbaLineChartTrackHost(lineChart)) {{
          ensurePumbaAccountPageStripeWhenNoFillers(lineChart, exp);
          requestAnimationFrame(function () {{
            requestAnimationFrame(function () {{ ensurePumbaAccountPageStripeWhenNoFillers(lineChart, exp); }});
          }});
        }} else {{
          applyManualPumbaLineChartSpending(lineChart);
        }}
      }}
    }});
    ensureDebitAccountLowerBlocks();
  }}

  function applyFinanalyticsFromTotals(d) {{
    const inc = Number(d && d.income);
    const exp = Number(d && d.expense);
    syncMobilePumbaPaymentHistory(inc, exp);
    if (isMybankRootPath()) {{
      if (isFinite(inc) && isFinite(exp)) window.__HOME_LAST_FIN = {{ income: inc, expense: exp }};
      patchHomeAllOperationsSpendingBlock(exp);
      window.__HOME_SUPPRESS_MO_UNTIL = Date.now() + 400;
      try {{ ensureHomeFinReassertObserver(); }} catch (eHf) {{}}
    }}
    const onHome = isMybankRootPath();
    if (!onHome && !ENABLE_BROWSER_FIN_DOM_PATCH) return;
    if (onHome || shouldPatchFinanalyticsDom()) {{
      collectSpendingFinCards().forEach(function (c) {{
        patchFinanalyticsCard(c, exp, 'Нет трат', 'Траты', false, onHome);
      }});
      let earnCards = document.querySelectorAll('[data-qa-type="click-area earning-card"]');
      if (!earnCards.length) earnCards = collectFinCardsBySubtitle('доход');
      earnCards.forEach(function (c) {{
        patchFinanalyticsCard(c, inc, 'Нет доходов', 'Доходы', true, onHome);
      }});
    }}
  }}

  function finTotalsForMybankHomeFromOperationsApi(d) {{
    const st = d && d.stats;
    if (!st) return null;
    if (st.list_income != null && st.list_expense != null) {{
      const li = Number(st.list_income);
      const le = Number(st.list_expense);
      if (isFinite(li) && isFinite(le)) return {{ income: li, expense: le }};
    }}
    if (st.home_mybank_income != null && st.home_mybank_expense != null) {{
      const hi = Number(st.home_mybank_income);
      const he = Number(st.home_mybank_expense);
      if (isFinite(hi) && isFinite(he)) return {{ income: hi, expense: he }};
    }}
    return null;
  }}

  function ensureHomeFinReassertObserver() {{
    if (window.__homeFinReassertMo || typeof MutationObserver === 'undefined') return;
    const reapply = function () {{
      if (!isMybankRootPath() || !window.__HOME_LAST_FIN) return;
      if (__homeFinPatchBusy) return;
      __homeFinPatchBusy = true;
      try {{
        const x = window.__HOME_LAST_FIN;
        syncMobilePumbaPaymentHistory(x.income, x.expense);
        patchHomeAllOperationsSpendingBlock(x.expense);
        window.__HOME_SUPPRESS_MO_UNTIL = Date.now() + 500;
      }} finally {{
        __homeFinPatchBusy = false;
      }}
    }};
    window.__homeFinReassertMo = new MutationObserver(function () {{
      if (!isMybankRootPath() || !window.__HOME_LAST_FIN) return;
      if (window.__HOME_SUPPRESS_MO_UNTIL && Date.now() < window.__HOME_SUPPRESS_MO_UNTIL) return;
      window.clearTimeout(__homeFinMoLock);
      __homeFinMoLock = window.setTimeout(reapply, 140);
    }});
    const r = document.body || document.documentElement;
    if (r) window.__homeFinReassertMo.observe(r, {{ childList: true, subtree: true, characterData: true }});
  }}

  function syncFinanalyticsCards() {{
    if (!shouldSyncFinanalyticsCards()) return;
    if (!isMybankRootPath()) {{
      window.__HOME_FIN_SEEDED_FROM_API = false;
      try {{ delete window.__HOME_LAST_FIN; }} catch (eCl) {{}}
    }}
    if (!isMybankRootPath() || !window.__HOME_FIN_SEEDED_FROM_API) {{
      applyFinanalyticsFromTotals(PANEL_TOTALS_SNAPSHOT);
    }}
    const now = Date.now();
    const minWait = isMybankRootPath() ? 120 : 480;
    if (now - __finCardLastFetch < minWait || __finCardInFlight) return;
    __finCardLastFetch = now;
    __finCardInFlight = true;
    const done = function () {{ __finCardInFlight = false; }};
    if (isMybankRootPath()) {{
      fetchJsonFirstOk(_panelUrlVariants(PANEL_OPERATIONS_URL))
        .then(function (d) {{
          const t = finTotalsForMybankHomeFromOperationsApi(d);
          if (t) {{
            window.__HOME_FIN_SEEDED_FROM_API = true;
            applyFinanalyticsFromTotals(t);
          }} else {{
            return fetchJsonFirstOk(_panelUrlVariants(PANEL_INCOME_EXPENSE_URL))
              .then(function (d2) {{
                window.__HOME_FIN_SEEDED_FROM_API = true;
                applyFinanalyticsFromTotals(d2);
              }})
              .catch(function () {{
                window.__HOME_FIN_SEEDED_FROM_API = false;
                applyFinanalyticsFromTotals(PANEL_TOTALS_SNAPSHOT);
              }});
          }}
        }})
        .catch(function () {{
          return fetchJsonFirstOk(_panelUrlVariants(PANEL_INCOME_EXPENSE_URL))
            .then(function (d) {{
              window.__HOME_FIN_SEEDED_FROM_API = true;
              applyFinanalyticsFromTotals(d);
            }})
            .catch(function () {{
              window.__HOME_FIN_SEEDED_FROM_API = false;
              applyFinanalyticsFromTotals(PANEL_TOTALS_SNAPSHOT);
            }});
        }})
        .finally(done);
      return;
    }}
    fetchJsonFirstOk(_panelUrlVariants(PANEL_INCOME_EXPENSE_URL))
      .then(function (d) {{ applyFinanalyticsFromTotals(d); }})
      .catch(function () {{ applyFinanalyticsFromTotals(PANEL_TOTALS_SNAPSHOT); }})
      .finally(done);
  }}

  const RUB_ICON_HTML = `
<span data-component-type="platform-ui" iconpath="&lt;svg viewBox=&quot;0 0 24 24&quot; xmlns=&quot;http://www.w3.org/2000/svg&quot; focusable=&quot;false&quot;&gt;&lt;defs&gt;&lt;linearGradient id=&quot;paint0_linear_1524_1586&quot; x1=&quot;3.8&quot; y1=&quot;3.8&quot; x2=&quot;19.2&quot; y2=&quot;19.2&quot; gradientUnits=&quot;userSpaceOnUse&quot;&gt;&lt;stop stop-color=&quot;currentColor&quot;/&gt;&lt;stop offset=&quot;1&quot; stop-opacity=&quot;.7&quot; stop-color=&quot;currentColor&quot;/&gt;&lt;/linearGradient&gt;&lt;/defs&gt;&lt;path fill-rule=&quot;evenodd&quot; clip-rule=&quot;evenodd&quot; d=&quot;M12 .5C5.649.5.5 5.649.5 12S5.649 23.5 12 23.5 23.5 18.351 23.5 12 18.351.5 12 .5ZM9 11V6h3.96c1.017 0 2.072.154 2.821.841C16.396 7.405 17 8.271 17 9.5c0 1.229-.604 2.095-1.218 2.659-.75.688-1.805.841-2.823.841H11.5v1.041H15A1.959 1.959 0 0 1 13.041 16H11.5v.063a2 2 0 0 1-2 2H9V16l-1.5-.041V15.5A1.46 1.46 0 0 1 9 14.041V13l-1.5-.041v-.5A1.46 1.46 0 0 1 9 11Zm4-3h-1.5v3H13s1.5.106 1.5-1.447C14.5 8 13 8 13 8Z&quot; fill=&quot;url(#paint0_linear_1524_1586)&quot;/&gt;&lt;/svg&gt;" data-qa-type="uikit/icon" class="abH-Kb5MJ" style="width: 40px; height: 40px; color: var(--tui-text-primary-on-dark);"><span class="bbH-Kb5MJ" style="background: var(--tui-background-accent-2);"></span><span data-qa-type="uikit/icon.content" class="cbH-Kb5MJ" role="presentation" style="width: 24px; height: 24px;"><svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" focusable="false"><defs><linearGradient id="dsId_manualAcct_linear_1524_1586" x1="3.8" y1="3.8" x2="19.2" y2="19.2" gradientUnits="userSpaceOnUse"><stop stop-color="currentColor"></stop><stop offset="1" stop-opacity=".7" stop-color="currentColor"></stop></linearGradient></defs><path fill-rule="evenodd" clip-rule="evenodd" d="M12 .5C5.649.5.5 5.649.5 12S5.649 23.5 12 23.5 23.5 18.351 23.5 12 18.351.5 12 .5ZM9 11V6h3.96c1.017 0 2.072.154 2.821.841C16.396 7.405 17 8.271 17 9.5c0 1.229-.604 2.095-1.218 2.659-.75.688-1.805.841-2.823.841H11.5v1.041H15A1.959 1.959 0 0 1 13.041 16H11.5v.063a2 2 0 0 1-2 2H9V16l-1.5-.041V15.5A1.46 1.46 0 0 1 9 14.041V13l-1.5-.041v-.5A1.46 1.46 0 0 1 9 11Zm4-3h-1.5v3H13s1.5.106 1.5-1.447C14.5 8 13 8 13 8Z" fill="url(#dsId_manualAcct_linear_1524_1586)"></path></svg></span></span>`;

  function isOperationsDetailPage() {{
    if (location.pathname.indexOf('/mybank') === -1) return false;
    const q = new URLSearchParams(location.search || '');
    if (q.get('operationId') || q.get('operation_id') || q.get('id')) return true;
    return !!document.querySelector('[data-qa-type="mobile-pumba-detail-sheet"], [data-qa-type="independent-pumba-operation-details-container"]');
  }}

  function shouldPatchOperationsList() {{
    return location.pathname.indexOf('/mybank/operations') !== -1 && !isOperationsDetailPage();
  }}

  function shouldPatchOperationsDetail() {{
    return isOperationsDetailPage();
  }}

  /* Карточка «Перевод/Пополнение» может быть в портале вне getOperationDetailsContainer — ищем по всему document. */
  function listDetailAccountOperationRoots() {{
    if (!isOperationsDetailPage()) return [];
    const out = [];
    const seen = new Set();
    document.querySelectorAll('[data-qa-type="mobile-pumba-account-operation"]').forEach(function (el) {{
      if (!el.querySelector('[data-qa-type="molecule-account-operation"]')) return;
      if (seen.has(el)) return;
      seen.add(el);
      out.push(el);
    }});
    return out;
  }}

  function hasNativeDetailAccountCardForInjectGate() {{
    if (!isOperationsDetailPage()) return false;
    const roots = listDetailAccountOperationRoots();
    for (let i = 0; i < roots.length; i++) {{
      const root = roots[i];
      if (root.getAttribute('data-manual-pumba-operation') === '1') continue;
      const wrap = root.closest('[data-qa-type="accountCardsShown-wrapper"]');
      if (wrap && wrap.getAttribute('data-manual-injected-account-cards') === '1') continue;
      return true;
    }}
    return false;
  }}

  function touchManualDetailStylesOrder() {{
    const st =
      document.getElementById('manual-detail-pumba-cards-v21')
      || document.getElementById('manual-detail-pumba-cards-v20')
      || document.getElementById('manual-detail-pumba-cards-v19')
      || document.getElementById('manual-detail-pumba-cards-v18')
      || document.getElementById('manual-detail-pumba-cards-v17')
      || document.getElementById('manual-detail-pumba-cards-v16')
      || document.getElementById('manual-detail-pumba-cards-v15')
      || document.getElementById('manual-detail-pumba-cards-v14')
      || document.getElementById('manual-detail-pumba-cards-v13')
      || document.getElementById('manual-detail-pumba-cards-v12')
      || document.getElementById('manual-detail-pumba-cards-v11')
      || document.getElementById('manual-detail-pumba-cards-v10')
      || document.getElementById('manual-detail-pumba-cards-v9')
      || document.getElementById('manual-detail-pumba-cards-v8')
      || document.getElementById('manual-detail-pumba-cards-v7')
      || document.getElementById('manual-detail-pumba-cards-v6');
    if (st && st.parentNode === document.head && document.head.lastElementChild !== st) {{
      try {{ document.head.appendChild(st); }} catch (eOrd) {{}}
    }}
  }}

  function injectManualDetailStyles() {{
    if (document.getElementById('manual-detail-pumba-cards-v21')) return;
    ['manual-detail-pumba-cards-v3', 'manual-detail-pumba-cards-v4', 'manual-detail-pumba-cards-v5', 'manual-detail-pumba-cards-v6', 'manual-detail-pumba-cards-v7', 'manual-detail-pumba-cards-v8', 'manual-detail-pumba-cards-v9', 'manual-detail-pumba-cards-v10', 'manual-detail-pumba-cards-v11', 'manual-detail-pumba-cards-v12', 'manual-detail-pumba-cards-v13', 'manual-detail-pumba-cards-v14', 'manual-detail-pumba-cards-v15', 'manual-detail-pumba-cards-v16', 'manual-detail-pumba-cards-v17', 'manual-detail-pumba-cards-v18', 'manual-detail-pumba-cards-v19', 'manual-detail-pumba-cards-v20'].forEach(function (lid) {{
      const legacy = document.getElementById(lid);
      if (legacy) {{
        try {{ legacy.remove(); }} catch (eL) {{}}
      }}
    }});
    const st = document.createElement('style');
    st.id = 'manual-detail-pumba-cards-v21';
    st.textContent = `
/* Инжект: ширина; горизонтальный padding даёт independent-pumba-operation-details-container — не дублировать */
[data-manual-injected-account-cards="1"][data-qa-type="accountCardsShown-wrapper"],
[data-manual-injected-account-cards="1"] {{
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
  display: block !important;
  margin: 0 0 22px 0 !important;
  padding: 0 !important;
  overflow-x: visible !important;
}}
/* Убрать огромный 20px gap под молекулой внутри шелла; зазор между карточками «Перевод» и «Реквизиты» — margin снизу обёртки */
[data-manual-injected-account-cards="1"] > [data-component-type="platform-ui"][style*="--gaps"] {{
  width: 100% !important;
  box-sizing: border-box !important;
  --gaps: 0px !important;
  gap: 0 !important;
}}
[data-manual-injected-account-cards="1"] [data-qa-type="uikit/NotificationStack"].abhURjxRW,
[data-manual-injected-account-cards="1"] .abeiuVKPb {{
  display: none !important;
}}
/* Карточка elevated — как в bottom sheet Т‑Банка (24px, --tui-shadow-medium) */
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"][data-surface="true"],
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"][data-surface="true"] {{
  position: relative !important;
  border-radius: 24px !important;
  background-color: var(--tui-background-elevation-1, #fff) !important;
  box-shadow: var(--tui-shadow-medium, 0px 6px 34px 0px #0000001f) !important;
}}
[data-manual-injected-account-cards="1"] [data-qa-type="tui/surface-layer"],
[data-panel-manual-black-card="1"] [data-qa-type="tui/surface-layer"] {{
  border-radius: inherit !important;
}}
/* Секции шапки и строки счёта — блочно, друг под другом (и инжект, и патч нативной карточки) */
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .bbIfdcMse,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .ebIfdcMse,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .bb82ltuCV,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .eb82ltuCV,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .bbyhDFZ1P,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] .ebyhDFZ1P,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .bbIfdcMse,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .ebIfdcMse,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .bb82ltuCV,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .eb82ltuCV,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .bbyhDFZ1P,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] .ebyhDFZ1P {{
  display: block !important;
  width: 100% !important;
  box-sizing: border-box !important;
}}
/* Паддинги «Перевод» / «Реквизиты» — из CSS приложения (bbyhDFZ1P, bb41GSHng, ab4U6BtRY и т.д.), здесь не дублируем */
[data-manual-bank-wrapper="1"] {{
  margin-top: 0 !important;
}}
[data-qa-type="independent-pumba-operation-details-container"] [data-qa-type="bankDetailsShown-wrapper"] {{
  margin-top: 24px !important;
}}
[data-qa-type="mobile-pumba-detail-sheet"] [data-qa-type="bankDetailsShown-wrapper"] {{
  margin-top: 24px !important;
}}
[data-manual-injected-account-cards="1"] + [data-qa-type="bankDetailsShown-wrapper"] {{
  margin-top: 0 !important;
}}
/* Только строка с h2 + «Справка» (не внешняя оболочка с вложенным header) */
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] [data-qa-type="tui/header.wrapper"]:has(> h2[data-qa-type="tui/header.title"]),
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] [data-qa-type="tui/header.wrapper"]:has(> h2[data-qa-type="tui/header.title"]) {{
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  justify-content: space-between !important;
  width: 100% !important;
  box-sizing: border-box !important;
  gap: 0.5rem !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] h2[data-qa-type="tui/header.title"],
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] h2[data-qa-type="tui/header.title"] {{
  flex: 1 1 auto !important;
  min-width: 0 !important;
  margin: 0 !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] [data-qa-type="tui/header.accessories"],
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] [data-qa-type="tui/header.accessories"] {{
  flex-shrink: 0 !important;
}}
/* Строка счёта: три колонки — иконка | текстовый столбец | шеврон */
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"],
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  align-items: center !important;
  gap: 12px !important;
  width: 100% !important;
  box-sizing: border-box !important;
  text-align: left !important;
}}
/* Иконка ₽ — ровно 40px, без пустого «поля» слева от подписей (как в витрине) */
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] > div:first-child,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] > div:first-child {{
  flex: 0 0 40px !important;
  width: 40px !important;
  min-width: 40px !important;
  max-width: 40px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  padding: 0 !important;
  margin: 0 !important;
  box-sizing: border-box !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ {{
  margin-left: 0 !important;
  padding-left: 4px !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"],
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] {{
  gap: 10px !important;
}}
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"] > div:nth-child(2),
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"] .gbDhaGPUV,
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"] .gbvaqWFmO,
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"] .gbYDLs9QJ,
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] > div:nth-child(2),
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] .gbDhaGPUV,
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] .gbvaqWFmO,
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] .gbYDLs9QJ {{
  flex: 1 1 auto !important;
  min-width: 0 !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: flex-start !important;
  justify-content: center !important;
  gap: 0.125rem !important;
}}
[data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"] > div:last-child,
[data-manual-injected-account-cards="1"] button[data-qa-type="tui/cell"] > div:last-child {{
  flex: 0 0 auto !important;
  margin-left: auto !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation-chevron"],
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation-chevron"] [data-qa-type="uikit/icon.content"],
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation-chevron"],
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation-chevron"] [data-qa-type="uikit/icon.content"] {{
  width: 8px !important;
  height: 16px !important;
  max-width: 8px !important;
  min-width: 8px !important;
  flex-shrink: 0 !important;
}}
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation-chevron"] svg,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation-chevron"] svg {{
  width: 8px !important;
  height: 16px !important;
  display: block !important;
}}
[data-panel-manual-black-card="1"] p[data-qa-type="molecule-account-operation-balance"],
[data-manual-injected-account-cards="1"] p[data-qa-type="molecule-account-operation-balance"] {{
  margin: 0 !important;
  min-width: 0 !important;
  width: 100% !important;
  font: var(--tui-typography-body-s, 400 13px/1.3846 Roboto, system-ui, sans-serif) !important;
  font-weight: 400 !important;
  color: var(--tui-text-secondary, #9299a2) !important;
}}
[data-panel-manual-black-card="1"] p[data-qa-type="molecule-account-operation-balance"] [data-qa-type="atom-sensitive"],
[data-manual-injected-account-cards="1"] p[data-qa-type="molecule-account-operation-balance"] [data-qa-type="atom-sensitive"] {{
  font: inherit !important;
  font-weight: 400 !important;
  color: inherit !important;
}}
/* Имя счёта (верхняя строка «Black»): regular, тёмно-серый как на витрине; не .gbYDLs9QJ span внутри p баланса */
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .ebvaqWFmO,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbvaqWFmO > .ebvaqWFmO,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbvaqWFmO > .ebvaqWFmO span,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .ebYDLs9QJ,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ > .ebYDLs9QJ,
[data-panel-manual-black-card="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ > .ebYDLs9QJ span,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .ebvaqWFmO,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbvaqWFmO > .ebvaqWFmO,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbvaqWFmO > .ebvaqWFmO span,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .ebYDLs9QJ,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ > .ebYDLs9QJ,
[data-manual-injected-account-cards="1"] [data-qa-type="molecule-account-operation"] button[data-qa-type="tui/cell"] .gbYDLs9QJ > .ebYDLs9QJ span {{
  font: var(--tui-typography-body-l, 400 16px/1.4375 Roboto, system-ui, sans-serif) !important;
  font-weight: 400 !important;
  color: #575B61 !important;
  -webkit-font-smoothing: antialiased !important;
}}
/* Реквизиты: ширина обёрток; типографика/тени/отступы — только из CSS Т‑Банка по классам atom-panel / hbQgksk7i */
[data-qa-type="bankDetailsShown-wrapper"][data-manual-bank-wrapper="1"],
[data-qa-type="bankDetailsShown-wrapper"]:has([data-manual-requisites-panel="1"]) {{
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
  overflow-x: visible !important;
}}
[data-manual-requisites-panel="1"][data-qa-type="mobile-pumba-requisites-operation"],
[data-manual-bank-wrapper="1"] [data-qa-type="mobile-pumba-requisites-operation"] {{
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}}
/* Строки реквизитов — чуть правее, вровень с началом заголовка «Реквизиты» */
[data-manual-bank-wrapper="1"] [data-qa-type="mobile-pumba-requisites-operation"] [data-qa-type="visible-requisites"],
[data-manual-requisites-panel="1"] [data-qa-type="visible-requisites"] {{
  padding-left: 4px !important;
  box-sizing: border-box !important;
}}
[data-qa-type="requisite"][data-manual-requisite-row="1"] {{
  width: 100% !important;
  max-width: 100% !important;
  box-sizing: border-box !important;
}}
[data-qa-type="requisite"][data-manual-requisite-row="1"] > div {{
  display: flex !important;
  flex-direction: column !important;
  align-items: flex-start !important;
  width: 100% !important;
  box-sizing: border-box !important;
}}
[data-qa-type="mobile-pumba-account-operation"][data-panel-manual-black-card="1"] button[data-qa-type="tui/cell"],
[data-manual-injected-account-cards="1"] [data-qa-type="mobile-pumba-account-operation"] button[data-qa-type="tui/cell"] {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important;
  align-items: center !important;
  gap: 10px !important;
  width: 100% !important;
  box-sizing: border-box !important;
  text-align: left !important;
}}
`;
    (document.head || document.documentElement).appendChild(st);
  }}

  function deepClone(v) {{
    return JSON.parse(JSON.stringify(v));
  }}

  function parseBankDate(value) {{
    if (!value || typeof value !== 'string') return Date.now();
    const m = value.match(/(\\d{{2}})\\.(\\d{{2}})\\.(\\d{{4}}),\\s*(\\d{{2}}):(\\d{{2}}):(\\d{{2}})/);
    if (!m) return Date.now();
    return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]), Number(m[4]), Number(m[5]), Number(m[6])).getTime();
  }}

  function moneyValue(node) {{
    if (node && typeof node === 'object' && typeof node.value === 'number') return node.value;
    if (typeof node === 'number') return node;
    return null;
  }}

  function operationKind(node) {{
    if (!node || typeof node !== 'object') return '';
    const t = node.type || node.operationType;
    if (t === 'Credit' || t === 'Debit') return t;
    const direction = String(node.direction || '').toUpperCase();
    if (direction === 'IN' || direction === 'INCOMING' || direction === 'CREDIT') return 'Credit';
    if (direction === 'OUT' || direction === 'OUTGOING' || direction === 'DEBIT') return 'Debit';
    const signed = moneyValue(node.signedAmount);
    if (typeof signed === 'number') return signed < 0 ? 'Debit' : signed > 0 ? 'Credit' : '';
    return '';
  }}

  function operationTime(node) {{
    if (!node || typeof node !== 'object') return 0;
    const ot = node.operationTime;
    if (ot && typeof ot === 'object') {{
      if (typeof ot.milliseconds === 'number') return ot.milliseconds;
      if (typeof ot.seconds === 'number') return Math.trunc(ot.seconds * 1000);
    }}
    for (const key of ['operationTimestamp', 'timestamp', 'time', 'dateTime']) {{
      const val = node[key];
      if (typeof val === 'number' && val > 0) return val > 1e12 ? val : Math.trunc(val * 1000);
    }}
    return parseBankDate(node.date || '');
  }}

  function isOperationRow(node) {{
    if (!node || typeof node !== 'object' || !node.id) return false;
    if (operationKind(node)) return true;
    if (node.operationTime && typeof node.operationTime === 'object') return true;
    for (const key of ['amount', 'operationAmount', 'accountAmount', 'paymentAmount', 'totalAmount']) {{
      if (moneyValue(node[key]) !== null) return true;
    }}
    return false;
  }}

  function isRelayEdges(list) {{
    if (!Array.isArray(list) || !list.length) return false;
    const first = list[0];
    return !!(first && typeof first === 'object' && first.node && isOperationRow(first.node));
  }}

  function listLooksLikeOps(list, key) {{
    if (!Array.isArray(list) || !list.length) return false;
    const first = list.find((x) => x && typeof x === 'object');
    if (!first) return false;
    if (isOperationRow(first)) return true;
    const low = String(key || '').toLowerCase();
    return ['operations', 'historyitems', 'transactions', 'feeditems', 'payload', 'items', 'data'].some((x) => low.indexOf(x) !== -1);
  }}

  function collectLists(root) {{
    const out = [];
    const seen = new Set();
    const hardSkip = ['cards', 'cardlist', 'bankcards', 'debitcards', 'creditcards', 'accounts', 'accountlist', 'products', 'productlist', 'offers'];

    function add(list) {{
      if (!Array.isArray(list)) return;
      if (seen.has(list)) return;
      seen.add(list);
      out.push(list);
    }}

    function shouldSkip(path, key) {{
      const dotted = '.' + path.concat([key]).join('.').toLowerCase() + '.';
      return hardSkip.some((frag) => dotted.indexOf('.' + frag + '.') !== -1);
    }}

    function walk(node, path) {{
      if (Array.isArray(node)) {{
        node.forEach((item) => {{
          if (item && typeof item === 'object') walk(item, path);
        }});
        return;
      }}
      if (!node || typeof node !== 'object') return;
      Object.keys(node).forEach((key) => {{
        const value = node[key];
        if (Array.isArray(value)) {{
          if (!shouldSkip(path, key) && (isRelayEdges(value) || listLooksLikeOps(value, key))) add(value);
          value.forEach((item) => {{
            if (item && typeof item === 'object') walk(item, path.concat([key]));
          }});
        }} else if (value && typeof value === 'object') {{
          walk(value, path.concat([key]));
        }}
      }});
    }}

    if (Array.isArray(root) && (isRelayEdges(root) || listLooksLikeOps(root, 'root'))) add(root);
    walk(root, []);
    return out;
  }}

  function setMoney(container, key, value) {{
    if (!container || typeof container !== 'object' || !(key in container)) return;
    const cur = container[key];
    if (cur && typeof cur === 'object') {{
      cur.value = value;
      if ('currency' in cur && !cur.currency) cur.currency = 'RUB';
    }} else {{
      container[key] = value;
    }}
  }}

  function setLogo(target, logo) {{
    if (!target || typeof target !== 'object' || !logo) return;
    ['logo', 'logoUrl', 'image', 'icon', 'picture', 'avatar', 'favicon'].forEach((key) => {{
      target[key] = logo;
    }});
  }}

  function overlayOperation(template, op, minMs) {{
    const out = deepClone(template);
    const typ = op.type === 'Credit' ? 'Credit' : 'Debit';
    const amt = Math.abs(Number(op.amount || 0));
    const preset = PRESETS[String(op.bank_preset || 'custom').toLowerCase()] || PRESETS.sbp || {{}};
    const primary = String(op.title || op.description || op.phone || (typ === 'Debit' ? 'Перевод' : 'Поступление')).trim();
    const bankName = String(op.bank || preset.name || 'Переводы').trim();
    const logo = preset.logo || (PRESETS.sbp && PRESETS.sbp.logo) || '';
    const ms = Math.max(parseBankDate(op.date || ''), Date.now(), (minMs || 0) + 1);
    const signed = typ === 'Debit' ? -amt : amt;

    out.id = op.id;
    if ('type' in out) out.type = typ;
    if ('operationType' in out) out.operationType = typ;
    if ('title' in out) out.title = primary;
    if ('name' in out) out.name = primary;
    if ('subtitle' in out) out.subtitle = 'Переводы';
    if ('formattedDescription' in out) out.formattedDescription = 'Переводы';
    if ('merchantName' in out) out.merchantName = bankName;

    setMoney(out, 'amount', amt);
    setMoney(out, 'operationAmount', amt);
    setMoney(out, 'accountAmount', amt);
    setMoney(out, 'paymentAmount', amt);
    setMoney(out, 'totalAmount', amt);
    setMoney(out, 'signedAmount', signed);
    setMoney(out, 'debitAmount', typ === 'Debit' ? amt : 0);
    setMoney(out, 'creditAmount', typ === 'Credit' ? amt : 0);

    out.group = 'TRANSFER';
    out.subgroup = {{ id: 'F1', name: 'Переводы' }};
    out.mcc = 0;
    out.mccString = '0000';
    out.isInner = false;
    out.hasShoppingReceipt = false;
    out.virtualPaymentType = 0;
    out.spendingCategory = {{ id: '24', name: 'Переводы', icon: 'transfers-c1', baseColor: '4FC5DF' }};
    out.categoryInfo = {{
      bankCategory: {{
        id: '24',
        language: 'ru',
        name: 'Переводы',
        baseColor: '4FC5DF',
        fileLink: 'https://brands-prod.cdn-tinkoff.ru/general_logo/transfers-c1.png'
      }}
    }};
    out.additionalInfo = [{{
      fieldName: 'Тип перевода',
      fieldValue: typ === 'Debit' ? 'Перевод в другой банк' : 'Перевод из другого банка'
    }}];

    out.operationTime = Object.assign({{}}, out.operationTime || {{}}, {{ milliseconds: ms, seconds: ms / 1000 }});
    out.date = op.date || out.date;
    if ('timestamp' in out) out.timestamp = ms;
    if ('operationTimestamp' in out) out.operationTimestamp = ms;
    if (typ === 'Credit') {{
      out.creditingTime = {{ milliseconds: ms }};
      delete out.debitingTime;
    }} else {{
      out.debitingTime = {{ milliseconds: ms }};
      delete out.creditingTime;
    }}

    if (out.counterparty && typeof out.counterparty === 'object') {{
      out.counterparty.name = primary;
      setLogo(out.counterparty, logo);
    }}
    if (out.merchant && typeof out.merchant === 'object') {{
      out.merchant.name = bankName;
      setLogo(out.merchant, logo);
    }}
    setLogo(out, logo);
    return out;
  }}

  function mergeIntoList(list) {{
    if (!Array.isArray(list) || !MANUAL_OPS.length) return false;
    let changed = false;
    if (isRelayEdges(list)) {{
      const firstEdge = list.find((x) => x && typeof x === 'object' && x.node && typeof x.node === 'object');
      const nodes = list.map((x) => x && x.node).filter((x) => x && typeof x === 'object');
      if (!firstEdge || !nodes.length) return false;
      let tick = Math.max.apply(null, nodes.map(operationTime).concat([0]));
      const existing = new Set(nodes.map((x) => x.id));
      MANUAL_OPS.forEach((op) => {{
        if (existing.has(op.id)) return;
        const edge = deepClone(firstEdge);
        edge.cursor = 'm_' + op.id;
        edge.node = overlayOperation(firstEdge.node, op, tick);
        tick = operationTime(edge.node);
        list.unshift(edge);
        existing.add(op.id);
        changed = true;
      }});
      list.sort((a, b) => operationTime((b && b.node) || {{}}) - operationTime((a && a.node) || {{}}));
      return changed;
    }}

    const template = list.find((x) => isOperationRow(x));
    if (!template) return false;
    let tick = Math.max.apply(null, list.filter((x) => isOperationRow(x)).map(operationTime).concat([0]));
    const existing = new Set(list.filter((x) => x && typeof x === 'object').map((x) => x.id));
    MANUAL_OPS.forEach((op) => {{
      if (existing.has(op.id)) return;
      const item = overlayOperation(template, op, tick);
      tick = operationTime(item);
      list.unshift(item);
      existing.add(op.id);
      changed = true;
    }});
    list.sort((a, b) => operationTime(b || {{}}) - operationTime(a || {{}}));
    return changed;
  }}

  function patchData(data, url) {{
    if (!shouldPatchOperationsList()) return data;
    if (!data || typeof data !== 'object') return data;
    const lists = collectLists(data);
    if (!lists.length) return data;
    const primary = lists.slice().sort((a, b) => b.length - a.length)[0];
    mergeIntoList(primary);
    return data;
  }}

  function formatPhoneRu(phone) {{
    const digits = String(phone || '').replace(/\\D/g, '');
    let normalized = digits;
    if (normalized.length === 11 && (normalized[0] === '7' || normalized[0] === '8')) {{
      normalized = '7' + normalized.slice(1);
    }} else if (normalized.length === 10) {{
      normalized = '7' + normalized;
    }}
    if (normalized.length !== 11 || normalized[0] !== '7') return String(phone || '').trim();
    return '+7 ' + normalized.slice(1, 4) + ' ' + normalized.slice(4, 7) + '-' + normalized.slice(7, 9) + '-' + normalized.slice(9, 11);
  }}

  function getDetailUrlOperationId() {{
    try {{
      const href = String(location.href || '');
      const q = new URLSearchParams(location.search || '');
      let opId = (q.get('operationId') || q.get('operation_id') || q.get('id') || '').trim();
      if (!opId && location.hash) {{
        const h = String(location.hash).replace(/^#/, '');
        const qi = h.indexOf('?');
        const hq = new URLSearchParams(qi >= 0 ? h.slice(qi + 1) : h);
        opId = (hq.get('operationId') || hq.get('operation_id') || hq.get('id') || '').trim();
      }}
      if (!opId) {{
        const m = href.match(/(?:[?&#])(?:operationId|operation_id)=([^&#'"\\s]+)/i);
        if (m) opId = m[1];
      }}
      if (!opId) return '';
      try {{
        opId = decodeURIComponent(opId);
      }} catch (e2) {{}}
      return String(opId).trim();
    }} catch (e) {{
      return '';
    }}
  }}

  function detectOperationAmountTextFromPage() {{
    const block = document.querySelector('[data-qa-type="tui/block-details"]');
    const amountNode = block
      ? block.querySelector('.gbURQcN_Z [data-qa-type="atom-sensitive"], [data-qa-type="atom-sensitive"]')
      : document.querySelector('[data-qa-type="tui/block-details"] [data-qa-type="atom-sensitive"]');
    return String(amountNode && amountNode.textContent || '').replace(/\\u00a0/g, ' ').trim();
  }}

  function amountDigitsForMatch(txt) {{
    return String(txt || '').replace(/\\D/g, '');
  }}

  function matchManualOpByDomHeuristic() {{
    if (!shouldPatchOperationsDetail()) return null;
    const t = detectOperationTypeFromPage();
    const title = String(detectOperationTitleFromPage() || '').trim();
    if (!title || !MANUAL_OPS.length) return null;
    const hits = MANUAL_OPS.filter(function (o) {{
      if (!o || String(o.type || 'Debit') !== t) return false;
      return String(o.title || '').trim() === title;
    }});
    if (hits.length === 1) return hits[0];
    if (hits.length > 1) {{
      const ad = amountDigitsForMatch(detectOperationAmountTextFromPage());
      if (ad.length) {{
        const narrowed = hits.filter(function (o) {{
          return amountDigitsForMatch(String(Math.abs(Number(o.amount || 0)))) === ad;
        }});
        if (narrowed.length === 1) return narrowed[0];
      }}
    }}
    return null;
  }}

  let __detailPanelFetchFor = '';
  function maybeFetchDetailOpFromPanel(opId) {{
    if (!opId || !PANEL_ORIGIN) return;
    if (__detailPanelFetchFor === opId) return;
    __detailPanelFetchFor = opId;
    fetchJsonFirstOk(_panelUrlVariants(PANEL_ORIGIN + '/api/operations'))
      .then(function (data) {{
        const list = (data && data.operations) || [];
        let row = list.find(function (x) {{ return x && String(x.id) === String(opId); }});
        if (!row) {{
          const pageType = detectOperationTypeFromPage();
          const pageTitle = String(detectOperationTitleFromPage() || '').trim();
          if (pageTitle) {{
            const cands = list.filter(function (x) {{
              return (
                x &&
                (x.manual || x.fake_transfer) &&
                String(x.type || 'Debit') === pageType &&
                String((x.title || x.desc || '')).trim() === pageTitle
              );
            }});
            if (cands.length === 1) {{
              row = cands[0];
            }} else if (cands.length > 1) {{
              const ad = amountDigitsForMatch(detectOperationAmountTextFromPage());
              if (ad.length) {{
                const narrowed = cands.filter(function (x) {{
                  return amountDigitsForMatch(String(Math.abs(Number(x.amount || 0)))) === ad;
                }});
                if (narrowed.length === 1) row = narrowed[0];
              }}
            }}
          }}
        }}
        if (row) {{
          DETAIL_OPS_BY_ID[opId] = {{
            source_id: row.id,
            type: row.type || 'Debit',
            title: String(row.title || row.desc || '').trim(),
            description: String(row.description || '').trim(),
            requisite_phone: String(row.requisite_phone || row.phone || '').trim(),
            phone: String(row.phone || '').trim(),
            requisite_sender_name: String(row.requisite_sender_name || row.sender_name || '').trim(),
            sender_name: String(row.sender_name || '').trim(),
            card_number: String(row.card_number || '').trim(),
            bank_preset: String(row.bank_preset || 'custom').toLowerCase(),
            bank: String(row.bank || '').trim(),
            manual: !!row.manual,
            fake_transfer: !!row.fake_transfer,
          }};
        }} else {{
          DETAIL_OPS_BY_ID[opId] = {{ _notFound: true }};
        }}
      }})
      .catch(function () {{
        if (!DETAIL_OPS_BY_ID[opId]) DETAIL_OPS_BY_ID[opId] = {{ _notFound: true }};
      }})
      .finally(function () {{
        if (__detailPanelFetchFor === opId) __detailPanelFetchFor = '';
        patchDetailDom();
      }});
  }}

  function resolveDetailOp() {{
    const opId = getDetailUrlOperationId();
    if (!opId) return null;
    const fromList = MANUAL_OPS.find(function (o) {{ return o && String(o.id) === String(opId); }});
    if (fromList) return fromList;
    const snap = DETAIL_OPS_BY_ID[opId];
    if (snap && snap._notFound) return matchManualOpByDomHeuristic();
    if (snap) {{
      const mid = snap.source_id != null ? String(snap.source_id) : opId;
      return Object.assign({{ id: mid }}, snap);
    }}
    return null;
  }}

  function currentManualOp() {{
    return resolveDetailOp();
  }}

  function receiptOpenUrlForOperationId(opId) {{
    if (!opId) return '';
    const origin = (typeof location !== 'undefined' && location.origin) ? String(location.origin).replace(/\\/$/, '') : '';
    if (origin) {{
      return origin + '/payment_receipt_pdf?operationId=' + encodeURIComponent(opId);
    }}
    return PANEL_ORIGIN + '/api/manual_operation_receipt?operationId=' + encodeURIComponent(opId);
  }}

  function bindManualCertReceiptClick() {{
    if (window.__manualCertReceiptClickBound) return;
    window.__manualCertReceiptClickBound = true;
    document.addEventListener(
      'click',
      function (ev) {{
        const btn = ev.target && ev.target.closest && ev.target.closest('button[data-qa-type="molecule-account-operation-cert-btn"]');
        if (!btn) return;
        if (!shouldPatchOperationsDetail()) return;
        const op = currentManualOp();
        const opId = (op && op.id) || getDetailUrlOperationId();
        if (!opId) return;
        ev.preventDefault();
        ev.stopPropagation();
        const url = receiptOpenUrlForOperationId(opId);
        if (!url) return;
        if (typeof location !== 'undefined' && location.origin && url.indexOf(location.origin) === 0) {{
          window.location.assign(url);
        }} else {{
          window.open(url, '_blank', 'noopener,noreferrer');
        }}
      }},
      true
    );
  }}

  function detectOperationTypeFromPage() {{
    const block = document.querySelector('[data-qa-type="tui/block-details"]');
    const amountNode = block
      ? block.querySelector('.gbURQcN_Z [data-qa-type="atom-sensitive"], [data-qa-type="atom-sensitive"]')
      : document.querySelector('.hbbXSKdZE [data-qa-type="atom-sensitive"], .hbbXSKdZE, .abtD8mgza');
    const txt = String(amountNode && amountNode.textContent || '').replace(/\u00A0/g, ' ').trim();
    if (!txt) return 'Debit';
    if (txt.indexOf('+') === 0) return 'Credit';
    return 'Debit';
  }}

  function detectOperationTitleFromPage() {{
    const block = document.querySelector('[data-qa-type="tui/block-details"]');
    const node = block
      ? block.querySelector('div[data-style-layer="primary"] span.bbnRJ7Txo, div[data-style-layer="primary"] span')
      : document.querySelector('.bbbXSKdZE .bbnFC5Q_W, [data-qa-type="tui/block-details"] .bbnFC5Q_W');
    return String(node && node.textContent || '').trim();
  }}

  function fallbackOpFromPage() {{
    const type = detectOperationTypeFromPage();
    const title = detectOperationTitleFromPage();
    let phone = '';
    const reqValue = document.querySelector('[data-qa-type="visible-requisites"] .ebQgksk7i, [data-qa-type="visible-requisites"] .ebw2AqQYk, [data-qa-type="visible-requisites"] .ebTpecb88, [data-qa-type="visible-requisites"] .ebKtz2I68');
    if (reqValue) phone = String(reqValue.textContent || '').trim();
    return {{
      id: '',
      type: type,
      title: title,
      requisite_sender_name: title,
      sender_name: title,
      requisite_phone: phone,
      phone: phone
    }};
  }}

  function fallbackOpFromScopedPage() {{
    const root =
      document.querySelector('[data-qa-type="independent-pumba-operation-details-container"]')
      || document.querySelector('[data-qa-type="mobile-pumba-detail-sheet"]')
      || document.body;
    const type = detectOperationTypeFromPage();
    const title = detectOperationTitleFromPage();
    let phone = '';
    const reqValue = root.querySelector('[data-qa-type="visible-requisites"] .ebQgksk7i, [data-qa-type="visible-requisites"] .ebw2AqQYk, [data-qa-type="visible-requisites"] .ebTpecb88, [data-qa-type="visible-requisites"] .ebKtz2I68');
    if (reqValue) phone = String(reqValue.textContent || '').trim();
    return {{
      id: '',
      type: type,
      title: title,
      requisite_sender_name: title,
      sender_name: title,
      requisite_phone: phone,
      phone: phone
    }};
  }}

  function getRequisiteParts(node) {{
    if (!node || typeof node.querySelectorAll !== 'function') return {{}};
    const labelEl = node.querySelector('p');
    let valueEl = null;

    if (labelEl && labelEl.parentElement) {{
      const siblings = Array.from(labelEl.parentElement.children || []);
      for (const sib of siblings) {{
        if (!sib || sib === labelEl) continue;
        const txt = String(sib.textContent || '').trim();
        if (txt) {{
          valueEl = sib;
          break;
        }}
      }}
    }}

    if (!valueEl) {{
      const candidates = Array.from(node.querySelectorAll('div, span'));
      for (const el of candidates) {{
        if (!el || el === labelEl) continue;
        const text = String(el.textContent || '').trim();
        if (!text) continue;
        if (labelEl && el.contains(labelEl)) continue;
        valueEl = el;
      }}
    }}
    return {{ labelEl, valueEl }};
  }}

  function ensureTransferBlackBadge(container, op) {{
    if (!container) return false;
    if (!op) return false;
    const kindLabel = op.type === 'Credit' ? 'Пополнение' : 'Перевод';
    const existing = container.querySelector('[data-manual-black-badge="1"]');
    const template = existing || container.querySelector('[data-qa-type="requisite"]');
    if (!template) return false;
    const badge = existing || template.cloneNode(true);
    const parts = getRequisiteParts(badge);
    if (parts.labelEl) parts.labelEl.textContent = kindLabel;
    if (parts.valueEl) parts.valueEl.textContent = 'Black';
    badge.setAttribute('data-manual-black-badge', '1');
    if (!existing) container.insertBefore(badge, container.firstChild);
    return true;
  }}

  function applyAccountCardBlackPatch(root, op) {{
    if (!root || !op) return false;
    const title = op.type === 'Credit' ? 'Пополнение' : 'Перевод';
    const titleWrap = root.querySelector('[data-qa-type="molecule-account-operation-title-text"]');
    if (titleWrap) {{
      const titleNode = findDetailAccountOperationTitleTextNode(titleWrap);
      if (titleNode) titleNode.textContent = title;
    }}
    const accountCell = root.querySelector('[data-qa-type="tui/cell"]');
    if (accountCell) {{
      try {{
        accountCell.style.setProperty('display', 'flex', 'important');
        accountCell.style.setProperty('flex-direction', 'row', 'important');
        accountCell.style.setProperty('flex-wrap', 'nowrap', 'important');
        accountCell.style.setProperty('align-items', 'center', 'important');
        accountCell.style.setProperty('width', '100%', 'important');
        accountCell.style.setProperty('box-sizing', 'border-box', 'important');
        const ch = accountCell.children;
        if (ch && ch.length >= 2) {{
          const mid = ch[1];
          if (mid && mid.style) {{
            mid.style.setProperty('display', 'flex', 'important');
            mid.style.setProperty('flex-direction', 'column', 'important');
            mid.style.setProperty('flex', '1 1 auto', 'important');
            mid.style.setProperty('min-width', '0', 'important');
            mid.style.setProperty('align-items', 'flex-start', 'important');
            mid.style.setProperty('justify-content', 'center', 'important');
          }}
        }}
      }} catch (eCellLayout) {{}}
      const iconNode = accountCell.querySelector('[data-qa-type="molecule-account-operation-account-icon"]');
      if (iconNode && !iconNode.querySelector('[data-qa-type="uikit/icon"] .bbH-Kb5MJ')) {{
        iconNode.innerHTML = RUB_ICON_HTML;
      }}
      const blackNode = findAccountCellCounterpartyNameNode(accountCell);
      if (blackNode) blackNode.textContent = 'Black';
    }}
    root.setAttribute('data-panel-manual-black-card', '1');
    applyBalanceTextToBlackAccountRows(BALANCE_TEXT);
    syncBlackAccountBalanceFromPanel();
    return true;
  }}

  function patchExistingTopOperationCard(op) {{
    if (!op) return false;
    let any = false;
    listDetailAccountOperationRoots().forEach(function (root) {{
      if (applyAccountCardBlackPatch(root, op)) any = true;
    }});
    return any;
  }}

  function removeAccountCardBlock(root) {{
    if (!root) return;
    const wrap = root.closest('[data-qa-type="accountCardsShown-wrapper"]');
    if (!wrap) {{
      try {{ root.remove(); }} catch (e0) {{}}
      return;
    }}
    let node = root;
    while (node.parentElement && node.parentElement !== wrap) {{
      node = node.parentElement;
    }}
    if (node.parentElement === wrap) {{
      try {{ node.remove(); }} catch (e1) {{}}
    }} else {{
      try {{ wrap.remove(); }} catch (e2) {{}}
      return;
    }}
    if (wrap.parentElement && wrap.children.length === 0) {{
      try {{ wrap.remove(); }} catch (e3) {{}}
    }}
  }}

  function ensureInjectedTopOperationCard(op) {{
    if (!op || !MANUAL_ACCOUNT_CARDS_SHELL_HTML) return false;
    const details = getOperationDetailsContainer();
    if (!details) return false;
    if (hasNativeDetailAccountCardForInjectGate()) return false;

    let shell = details.querySelector('[data-manual-injected-account-cards="1"]');
    if (!shell) {{
      details.querySelectorAll('[data-qa-type="accountCardsShown-wrapper"]').forEach(function (w) {{
        if (w.getAttribute('data-manual-injected-account-cards') === '1') return;
        if (w.querySelector('[data-qa-type="mobile-pumba-account-operation"]')) return;
        if (w.children.length === 0) {{
          try {{ w.remove(); }} catch (eRm) {{}}
        }}
      }});
      const tmp = document.createElement('div');
      tmp.innerHTML = MANUAL_ACCOUNT_CARDS_SHELL_HTML;
      shell = tmp.firstElementChild;
      if (!shell) return false;
      shell.setAttribute('data-manual-injected-account-cards', '1');
      const bankW = details.querySelector('[data-qa-type="bankDetailsShown-wrapper"]');
      if (bankW && bankW.parentElement) {{
        bankW.parentElement.insertBefore(shell, bankW);
      }} else {{
        const actions = details.querySelector('[data-qa-type="mobile-pumba-actions-operation"]');
        if (actions && actions.parentElement) {{
          let anchor = actions;
          while (anchor.parentElement && anchor.parentElement !== details) {{
            anchor = anchor.parentElement;
          }}
          if (anchor.parentElement === details) {{
            if (anchor.nextSibling) {{
              details.insertBefore(shell, anchor.nextSibling);
            }} else {{
              details.appendChild(shell);
            }}
          }} else {{
            const p = actions.parentElement;
            if (actions.nextSibling) {{
              p.insertBefore(shell, actions.nextSibling);
            }} else {{
              p.appendChild(shell);
            }}
          }}
        }} else {{
          details.appendChild(shell);
        }}
      }}
    }}
    const root = shell.querySelector('[data-qa-type="mobile-pumba-account-operation"]');
    if (!root) return false;
    root.setAttribute('data-manual-pumba-operation', '1');
    return applyAccountCardBlackPatch(root, op);
  }}

  function ensureDetailActionButtons(op) {{
    document.querySelectorAll('[data-manual-actions="1"]').forEach((n) => n.remove());
    const orphanWrap = document.querySelector('[data-manual-actions-wrapper="1"]');
    if (orphanWrap && orphanWrap.children.length === 0) orphanWrap.remove();

    const pumba = document.querySelector('[data-qa-type="mobile-pumba-actions-operation"]');
    if (!pumba) return false;

    const portalInner = pumba.querySelector('.bbgyrAMeC');
    let gapsRow = null;
    if (portalInner) {{
      gapsRow = portalInner.querySelector('div[data-component-type="platform-ui"][style*="--gaps: 12px"]')
        || portalInner.querySelector('div[style*="--gaps: 12px"]');
    }}
    if (!gapsRow) {{
      gapsRow = pumba.querySelector('div[data-component-type="platform-ui"][style*="--gaps: 12px"]')
        || pumba.querySelector('div[style*="--gaps: 12px"]');
    }}
    if (!gapsRow) return false;

    const isCredit = op && op.type === 'Credit';
    const desiredMode = isCredit ? 'credit' : 'debit';
    const prevMode = gapsRow.getAttribute('data-manual-tui-actions-mode') || '';

    if (isCredit) {{
      if (!MANUAL_ACTIONS_DISALLOW_ONLY_INNER_HTML) return false;
      const creditOk =
        prevMode === 'credit' &&
        gapsRow.getAttribute('data-manual-tui-actions-row') === '1' &&
        gapsRow.querySelector('button[data-qa-type="operation-action-disallow"]') &&
        !gapsRow.querySelector('button[data-qa-type="operation-action-split"]');
      if (creditOk) return true;
      gapsRow.setAttribute('data-manual-tui-actions-row', '1');
      gapsRow.setAttribute('data-manual-tui-actions-mode', 'credit');
      gapsRow.style.justifyContent = 'center';
      gapsRow.style.overflowX = 'hidden';
      gapsRow.innerHTML = MANUAL_ACTIONS_DISALLOW_ONLY_INNER_HTML;
      return true;
    }}

    if (!MANUAL_ACTIONS_ROW_INNER_HTML) return false;
    const labels = ['Избранное', 'Повторить', 'Не учитывать', 'Оспорить', 'Разделить'];
    const txt = String(gapsRow.textContent || '').replace(/\\s+/g, ' ');
    const debitOk =
      prevMode === 'debit' &&
      gapsRow.getAttribute('data-manual-tui-actions-row') === '1' &&
      labels.every((l) => txt.indexOf(l) !== -1) &&
      gapsRow.querySelector('button[data-qa-type="operation-action-split"]');
    if (debitOk) return true;

    gapsRow.setAttribute('data-manual-tui-actions-row', '1');
    gapsRow.setAttribute('data-manual-tui-actions-mode', 'debit');
    gapsRow.style.justifyContent = '';
    gapsRow.style.overflowX = '';
    gapsRow.innerHTML = MANUAL_ACTIONS_ROW_INNER_HTML;
    return true;
  }}

  function makeManualRequisiteRow(label, value) {{
    const wrap = document.createElement('div');
    wrap.setAttribute('data-manual-requisite-row', '1');
    wrap.setAttribute('data-qa-type', 'requisite');
    wrap.setAttribute('data-interactive', 'false');
    wrap.setAttribute('data-height-mode', 'default');
    wrap.setAttribute('data-horizontal-spacing', 'none');
    wrap.setAttribute('data-vertical-spacing', 'default');
    wrap.setAttribute('data-connected', 'false');
    wrap.setAttribute('data-component-type', 'tui-react');
    wrap.className = 'hbQgksk7i';
    const inner = document.createElement('div');
    inner.className = 'gbQgksk7i';
    const p = document.createElement('p');
    p.className = 'dbQgksk7i';
    p.textContent = label;
    const val = document.createElement('div');
    val.className = 'ebQgksk7i abhFnGE_2';
    val.textContent = value;
    inner.appendChild(p);
    inner.appendChild(val);
    wrap.appendChild(inner);
    return wrap;
  }}

  function dedupeDetailAccountCards() {{
    if (!isOperationsDetailPage()) return;
    const allRoots = listDetailAccountOperationRoots();
    if (allRoots.length > 1) {{
      /* Порядок document — нижняя карточка последняя; верхняя часто в портале вне detail-контейнера. */
      for (let i = 0; i < allRoots.length - 1; i++) {{
        removeAccountCardBlock(allRoots[i]);
      }}
      return;
    }}
    const details = getOperationDetailsContainer();
    if (!details) return;
    const wrap = details.querySelector('[data-qa-type="accountCardsShown-wrapper"]');
    if (!wrap) return;
    const rows = Array.from(wrap.children || []).filter(function (el) {{
      return el && el.querySelector && el.querySelector('[data-qa-type="mobile-pumba-account-operation"]');
    }});
    if (rows.length <= 1) return;
    const keepRow = rows[rows.length - 1];
    rows.forEach(function (r) {{
      if (r !== keepRow) {{
        try {{ r.remove(); }} catch (e1) {{}}
      }}
    }});
  }}

  function dedupeDetailRequisitesBlocks() {{
    const wrap = document.querySelector('[data-qa-type="bankDetailsShown-wrapper"]');
    if (!wrap || wrap.getAttribute('data-manual-bank-wrapper') === '1') return;
    const rows = Array.from(wrap.children || []).filter(function (el) {{
      return el && el.querySelector && el.querySelector('[data-qa-type="mobile-pumba-requisites-operation"]');
    }});
    if (rows.length <= 1) return;
    let best = rows[0];
    let bestLen = String(best.textContent || '').length;
    for (let i = 1; i < rows.length; i++) {{
      const L = String(rows[i].textContent || '').length;
      if (L > bestLen) {{ best = rows[i]; bestLen = L; }}
    }}
    rows.forEach(function (r) {{
      if (r !== best) {{
        try {{ r.remove(); }} catch (e2) {{}}
      }}
    }});
  }}

  function findNativeVisibleRequisites() {{
    const w = document.querySelector('[data-qa-type="bankDetailsShown-wrapper"]:not([data-manual-bank-wrapper="1"])');
    if (w) {{
      const vr = w.querySelector('[data-qa-type="visible-requisites"]');
      if (vr) return vr;
    }}
    const root =
      document.querySelector('[data-qa-type="independent-pumba-operation-details-container"]')
      || document.querySelector('[data-qa-type="mobile-pumba-detail-sheet"]');
    if (root) {{
      const scoped = root.querySelector(
        '[data-qa-type="bankDetailsShown-wrapper"]:not([data-manual-bank-wrapper="1"]) [data-qa-type="visible-requisites"]'
      );
      if (scoped) return scoped;
      const all = root.querySelectorAll('[data-qa-type="visible-requisites"]');
      for (let i = 0; i < all.length; i++) {{
        const el = all[i];
        if (el.closest('[data-manual-requisites-panel="1"]')) continue;
        if (el.closest('[data-manual-bank-wrapper="1"]')) continue;
        return el;
      }}
    }}
    return null;
  }}

  function isPlaceholderRequisiteValue(txt) {{
    const t = String(txt || '').replace(/\\s+/g, ' ').trim();
    if (!t || t.length <= 1) return true;
    if (t === '—' || t === '-' || t === '–') return true;
    const low = t.toLowerCase();
    if (low === 'null' || low === 'undefined') return true;
    return false;
  }}

  function visibleRequisitesNeedManualFill(vr) {{
    if (!vr) return true;
    const reqs = vr.querySelectorAll('[data-qa-type="requisite"]');
    if (!reqs.length) return true;
    for (let i = 0; i < reqs.length; i++) {{
      const parts = getRequisiteParts(reqs[i]);
      const val = String(parts.valueEl && parts.valueEl.textContent || '');
      if (!isPlaceholderRequisiteValue(val)) return false;
    }}
    return true;
  }}

  function ensureManualRequisitesPanel(op) {{
    if (!op) return false;
    const phoneFmt = formatPhoneRu(op.requisite_phone || op.phone || '');
    const senderText = String(op.requisite_sender_name || op.sender_name || op.title || '').trim();

    const nativeVr = findNativeVisibleRequisites();
    if (nativeVr && visibleRequisitesNeedManualFill(nativeVr)) {{
      if (op.type === 'Credit' && senderText) {{
        nativeVr.innerHTML = '';
        nativeVr.appendChild(makeManualRequisiteRow('Отправитель', senderText));
        return true;
      }}
      if (op.type === 'Debit' && phoneFmt) {{
        nativeVr.innerHTML = '';
        nativeVr.appendChild(makeManualRequisiteRow('Номер телефона', phoneFmt));
        return true;
      }}
    }}
    if (nativeVr && !visibleRequisitesNeedManualFill(nativeVr)) {{
      return false;
    }}

    let host = document.querySelector('[data-qa-type="bankDetailsShown-wrapper"]');
    if (!host) {{
      const detailsContainer = document.querySelector('[data-qa-type="independent-pumba-operation-details-container"]');
      if (!detailsContainer) return false;
      host = document.createElement('div');
      host.setAttribute('data-qa-type', 'bankDetailsShown-wrapper');
      host.setAttribute('data-manual-bank-wrapper', '1');
      host.className = 'abVXAIVX5';
      host.setAttribute('data-component-type', 'platform-ui');
      host.innerHTML = MANUAL_BANK_DETAILS_INNER_HTML;
      detailsContainer.appendChild(host);
    }}

    let panel = host.querySelector('[data-manual-requisites-panel="1"]');
    if (!panel) {{
      if (host.getAttribute('data-manual-bank-wrapper') === '1') {{
        host.className = 'abVXAIVX5';
        host.setAttribute('data-component-type', 'platform-ui');
        host.innerHTML = MANUAL_BANK_DETAILS_INNER_HTML;
      }} else {{
        const gapsRows = Array.from(host.querySelectorAll('div[data-component-type="platform-ui"][style*="--gaps: 20px"]'));
        let gap = null;
        for (let i = gapsRows.length - 1; i >= 0; i--) {{
          if (gapsRows[i].querySelector('[data-qa-type="mobile-pumba-requisites-operation"]')) {{
            gap = gapsRows[i];
            break;
          }}
        }}
        if (!gap && gapsRows.length) {{
          gap = gapsRows[gapsRows.length - 1];
        }}
        if (!gap) {{
          gap = host.querySelector('.abVdrB8kC') || host.querySelector('.abXrZFFIQ');
        }}
        if (gap) {{
          const tmp = document.createElement('div');
          tmp.innerHTML = MANUAL_BANK_DETAILS_INNER_HTML;
          const outer = tmp.firstElementChild;
          if (outer) {{
            while (outer.firstChild) gap.appendChild(outer.firstChild);
          }}
        }} else {{
          host.insertAdjacentHTML('beforeend', MANUAL_BANK_DETAILS_INNER_HTML);
        }}
      }}
      panel = host.querySelector('[data-manual-requisites-panel="1"]');
    }}
    if (!panel) return false;

    const vr = panel.querySelector('[data-qa-type="visible-requisites"]');
    if (!vr) return false;

    if (op.type === 'Credit') {{
      if (!senderText) return false;
      vr.innerHTML = '';
      vr.appendChild(makeManualRequisiteRow('Отправитель', senderText));
      return true;
    }}

    if (op.type === 'Debit') {{
      if (!phoneFmt) return false;
      vr.innerHTML = '';
      vr.appendChild(makeManualRequisiteRow('Номер телефона', phoneFmt));
      return true;
    }}

    return false;
  }}

  function patchDetailDom() {{
    if (!shouldPatchOperationsDetail()) return;
    injectManualDetailStyles();
    const opId = getDetailUrlOperationId();
    let op = resolveDetailOp();
    if (opId) {{
      if (!op) {{
        if (DETAIL_OPS_BY_ID[opId] && DETAIL_OPS_BY_ID[opId]._notFound) {{
          op = Object.assign({{ id: opId }}, fallbackOpFromScopedPage());
          const hasReq =
            !!(op.requisite_phone || op.phone || op.requisite_sender_name || op.sender_name || op.title);
          if (!hasReq) return;
        }} else {{
          maybeFetchDetailOpFromPanel(opId);
          return;
        }}
      }}
    }} else {{
      op = op || fallbackOpFromPage();
    }}
    if (!op) return;
    const manualLike = isManualLikeDetailOp(op);
    if (!manualLike) {{
      removeManualDetailArtifacts();
      return;
    }}
    dedupeDetailAccountCards();
    dedupeDetailRequisitesBlocks();
    const senderText = String(op.requisite_sender_name || op.sender_name || op.title || op.description || '').trim();
    const phoneText = formatPhoneRu(op.requisite_phone || op.phone || '');
    const blocks = Array.from(document.querySelectorAll('[data-qa-type="visible-requisites"]'));
    blocks.forEach((block) => {{
      Array.from(block.querySelectorAll('[data-qa-type="requisite"]')).forEach((req) => {{
        const parts = getRequisiteParts(req);
        const label = String(parts.labelEl && parts.labelEl.textContent || '').trim().toLowerCase();
        if (!label || !parts.valueEl) return;
        if (op.type === 'Debit' && phoneText) {{
          if (label.indexOf('отправител') !== -1 || label.indexOf('sender') !== -1) {{
            parts.labelEl.textContent = 'Номер телефона';
            parts.valueEl.textContent = phoneText;
            return;
          }}
          if (label.indexOf('номер телефона') !== -1 || label.indexOf('phone') !== -1 || label.indexOf('телефон') !== -1) {{
            parts.labelEl.textContent = 'Номер телефона';
            parts.valueEl.textContent = phoneText;
            return;
          }}
        }}
        if (op.type === 'Credit' && senderText) {{
          if (label.indexOf('номер телефона') !== -1 || label.indexOf('phone') !== -1 || label.indexOf('телефон') !== -1) {{
            parts.labelEl.textContent = 'Отправитель';
            parts.valueEl.textContent = senderText;
            return;
          }}
          if (label.indexOf('отправител') !== -1 || label.indexOf('sender') !== -1) {{
            parts.labelEl.textContent = 'Отправитель';
            parts.valueEl.textContent = senderText;
            return;
          }}
        }}
        if (op.type === 'Debit' && label.indexOf('получател') !== -1) {{
          req.remove();
        }}
      }});
    }});
    ensureDetailActionButtons(op);
    patchExistingTopOperationCard(op);
    ensureInjectedTopOperationCard(op);
    ensureManualRequisitesPanel(op);
    dedupeDetailAccountCards();
    dedupeDetailRequisitesBlocks();
    applyBalanceTextToBlackAccountRows(BALANCE_TEXT);
    syncBlackAccountBalanceFromPanel();
  }}

  function looksLikeOperationsRequest(url) {{
    const low = String(url || '').toLowerCase();
    if (!low) return false;
    return ['operations', 'operation', 'history', 'feed', 'transaction', 'statement', 'movement', 'registry', 'transfer', 'sbp', 'p2p', 'me2me', 'graphql']
      .some((x) => low.indexOf(x) !== -1);
  }}

  const originalFetch = window.fetch ? window.fetch.bind(window) : null;
  if (originalFetch) {{
    window.fetch = async function () {{
      const response = await originalFetch.apply(this, arguments);
      try {{
        if (!shouldPatchOperationsList()) return response;
        const req = arguments[0];
        const reqUrl = typeof req === 'string' ? req : (req && req.url) || '';
        const finalUrl = response.url || reqUrl;
        if (!looksLikeOperationsRequest(finalUrl)) return response;
        const contentType = String((response.headers && response.headers.get && response.headers.get('content-type')) || '').toLowerCase();
        if (contentType.indexOf('json') === -1 && contentType.indexOf('graphql') === -1) return response;
        const originalJson = response.json.bind(response);
        response.json = async function () {{
          const data = await originalJson();
          try {{
            return patchData(data, finalUrl);
          }} catch (e) {{
            return data;
          }}
        }};
      }} catch (e) {{
      }}
      return response;
    }};
  }}

  function startDetailDomPatcher() {{
    injectManualDetailStyles();
    patchDetailDom();
    let timer = 0;
    const schedulePatch = function () {{
      clearTimeout(timer);
      timer = window.setTimeout(patchDetailDom, 42);
    }};
    const observer = new MutationObserver(schedulePatch);
    if (document.body) {{
      observer.observe(document.body, {{ childList: true, subtree: true }});
    }}
    try {{
      window.addEventListener('popstate', patchDetailDom, {{ passive: true }});
    }} catch (ePs) {{}}
    window.setInterval(patchDetailDom, 1100);
  }}

  function startFinanalyticsCardSync() {{
    function tick() {{
      syncFinanalyticsCards();
      try {{ ensureDebitAccountLowerBlocks(); }} catch (eTail) {{}}
    }}
    tick();
    let __finMoTimer = 0;
    function scheduleFromDom() {{
      window.clearTimeout(__finMoTimer);
      __finMoTimer = window.setTimeout(tick, 140);
    }}
    try {{
      const moRoot =
        document.querySelector('main[data-qa-type="mobile-ib-container"]')
        || document.querySelector('main')
        || document.body;
      const mo = new MutationObserver(scheduleFromDom);
      mo.observe(moRoot, {{ childList: true, subtree: true }});
    }} catch (eMo) {{}}
    window.setInterval(tick, 900);
  }}

  bindManualCertReceiptClick();
  bindOrderCertificateStatementsClick();

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', function () {{
      startDetailDomPatcher();
      startFinanalyticsCardSync();
    }}, {{ once: true }});
  }} else {{
    startDetailDomPatcher();
    startFinanalyticsCardSync();
  }}
}})();
</script>
"""


def response(flow: http.HTTPFlow) -> None:
    history.ensure_manual_operations_fresh()
    if not is_bank_flow(flow):
        return
    if not flow.response:
        return
    ensure_response_decoded(flow)
    url = (flow.request.pretty_url or "").lower()
    if "/mybank" not in url:
        return
    # Не вмешиваться в HTML «Справок» — тяжёлый скрипт + CSP; диагностика «страница не грузится».
    if "/mybank/statements" in url or "mybank%2fstatements" in url:
        return
    content_type = (flow.response.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return
    html = flow.response.text or ""
    if not html or "__manualOpsBrowserInjector" in html:
        return
    script = _build_script()
    if "</body>" in html:
        html = html.replace("</body>", script + "\n</body>", 1)
    else:
        html += script
    html = re.sub(
        r'<meta[^>]+http-equiv=["\']Content-Security-Policy["\'][^>]*>',
        "",
        html,
        flags=re.IGNORECASE,
    )
    flow.response.text = html
    flow.response.headers.pop("Content-Security-Policy", None)
    flow.response.headers.pop("content-security-policy", None)
    flow.response.headers.pop("Content-Security-Policy-Report-Only", None)
    flow.response.headers.pop("content-security-policy-report-only", None)
