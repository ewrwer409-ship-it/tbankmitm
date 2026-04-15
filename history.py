from mitmproxy import http
import json
import copy
from typing import Optional
from datetime import datetime
from urllib.parse import parse_qs, urlparse
import time
import re
import os
import sys
import uuid
import base64
import controller
import func

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_filter import (
    is_bank_flow,
    ensure_response_decoded,
    bank_debug_enabled,
    is_jsonish_response,
    text_indicates_statements_spravki,
    url_prohibit_proxy_json_mutation,
)

operations_cache = {}
manual_operations = {}
hidden_operations = set()
last_sync_time = None
_manual_ops_mtime = None

# Последняя сводка доход/расход из ответа API Т‑Банка (как на /mybank/operations), до подмены.
_bank_histogram_income = None
_bank_histogram_expense = None


def extract_histogram_totals_from_payload(data):
    """Читает earning.summary.value / spending.summary.value из JSON гистограммы."""
    inc = exp = None

    def walk(obj):
        nonlocal inc, exp
        if isinstance(obj, dict):
            e = obj.get("earning")
            if isinstance(e, dict) and inc is None:
                s = e.get("summary")
                if isinstance(s, dict) and "value" in s:
                    try:
                        inc = float(s["value"])
                    except (TypeError, ValueError):
                        pass
            sp = obj.get("spending")
            if isinstance(sp, dict) and exp is None:
                s = sp.get("summary")
                if isinstance(s, dict) and "value" in s:
                    try:
                        exp = float(s["value"])
                    except (TypeError, ValueError):
                        pass
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return inc, exp


def record_bank_histogram_from_payload(data):
    """Сохранить цифры сводки с сайта для панели и подмены (вызывать до replace_histogram)."""
    global _bank_histogram_income, _bank_histogram_expense
    inc, exp = extract_histogram_totals_from_payload(data)
    if inc is not None:
        _bank_histogram_income = round(float(inc), 2)
    if exp is not None:
        _bank_histogram_expense = round(float(exp), 2)
    if inc is not None or exp is not None:
        print(f"[history] Сводка с сайта: доходы={_bank_histogram_income}, расходы={_bank_histogram_expense}")


def get_bank_histogram_totals():
    return _bank_histogram_income, _bank_histogram_expense


def panel_include_all_cached_operations() -> bool:
    """Панель: список и расчёт сумм по всем операциям в кэше прокси (до лимита), не только текущий месяц."""
    return bool((controller.config.get("history") or {}).get("panel_show_all_operations", True))


def get_panel_chart_display_totals():
    """Единые доход/расход для панели и подмены гистограмм: если в config.manual заданы income/expense — они главные;
    иначе при histogram_sync_with_operations: сводка банка + только ручные/мок‑переводы; без кэша реальных операций."""
    restrict_month = not panel_include_all_cached_operations()
    real_inc, real_exp, inc_cnt, exp_cnt = calculate_manual_and_mock_transfer_stats(restrict_month=restrict_month)
    manual = controller.config.get("manual") or {}
    b_inc, b_exp = get_bank_histogram_totals()
    transfer_exp_addon = _panel_transfer_expense_addon()
    # Доп. к сводке банка только за текущий месяц (как у гистограммы /mybank).
    man_inc_m, man_exp_m = _manual_operations_income_expense_month(restrict_month=True)
    fake_inc_m = _fake_credit_month_total()

    def pick(mkey, bank_v, real_v):
        sync_ops = manual.get("histogram_sync_with_operations", True)
        # Явно введённые в панели доход/расход всегда главные для гистограммы и блоков на mybank.
        # Сброс поля (null) в панели возвращает расчёт по операциям/банку ниже.
        if manual.get(mkey) is not None:
            try:
                return round(float(manual[mkey]), 2)
            except (TypeError, ValueError):
                pass
        use_bank = manual.get("panel_sync_bank_histogram", True)
        if hidden_operations and use_bank:
            use_bank = False
        if sync_ops:
            if use_bank and bank_v is not None:
                v = round(float(bank_v), 2)
                if mkey == "expense":
                    if transfer_exp_addon:
                        v = round(v + transfer_exp_addon, 2)
                    v = round(v + man_exp_m, 2)
                elif mkey == "income":
                    v = round(v + man_inc_m + fake_inc_m, 2)
                return v
            return round(float(real_v), 2)
        try:
            return round(float(manual.get(mkey, real_v)), 2)
        except (TypeError, ValueError):
            return round(float(real_v), 2)

    di = pick("income", b_inc, real_inc)
    de = pick("expense", b_exp, real_exp)
    return di, de, inc_cnt, exp_cnt


def sync_panel_income_expense_with_operations():
    """
    После добавления/изменения/удаления ручной или мок‑операции: убрать зафиксированные
    в config.manual суммы доход/расход и включить синхронизацию с операциями.
    Тогда панель и подмена гистограмм (panel_bridge) подхватывают новые суммы автоматически.
    """
    try:
        manual = controller.config.setdefault("manual", {})
        changed = False
        if "income" in manual:
            manual.pop("income", None)
            changed = True
        if "expense" in manual:
            manual.pop("expense", None)
            changed = True
        if manual.get("histogram_sync_with_operations") is False:
            manual["histogram_sync_with_operations"] = True
            changed = True
        if changed:
            controller.save_config()
            di, de, _, _ = get_panel_chart_display_totals()
            print(f"[history] Статистика синхронизирована с операциями: доходы={di} ₽, расходы={de} ₽")
    except Exception as e:
        print(f"[history] sync_panel_income_expense_with_operations: {e}")


# Параллельные запросы главной (браузер): схлопываем повторную вставку одного op_id.
_CROSS_RESPONSE_INJECT_MONO = {}  # op_id -> monotonic time of last insert

# Пути/строка запроса: браузер + api.tbank.ru / tinkoff — только явные «ленты».
_BROWSER_TBANK_INJECT_PATH_OK = (
    "graphql",
    "/operations",
    "/operation",
    "operation?",
    "history",
    "movement",
    # не "statement" — совпадает с "statements" (Справки); ниже точечные варианты
    "/statement/",
    "statement?",
    "=statement",
    "&statement",
    "registry",
    "extract",
    "transaction",
    "money-session",
    "aggregated",
    "receipt",
    "fiscal",
    "light_ib",
    "lightib",
    "payment_session",
    "phonetransfer",
    "phone-transfer",
    "me2me",
    "p2p",
    "sbp",
)


def _url_is_mybank_certificates_statements_spa(url: str) -> bool:
    """SPA «Справки» /mybank/statements — не путать с выпиской/statement в API (statement ⊂ statements)."""
    u = (url or "").lower()
    if "/mybank/statements" in u:
        return True
    if "mybank%2fstatements" in u:
        return True
    return False


def _flow_is_statements_certificates_context(
    url: str, referer: Optional[str] = None, request_text: Optional[str] = None
) -> bool:
    """Запросы с экрана «Справки», в т.ч. XHR: Referer часто без path — смотрим тело GraphQL."""
    return text_indicates_statements_spravki(url or "", referer or "", request_text or "")


def _ua_looks_like_desktop_browser(user_agent: Optional[str]) -> bool:
    ua = (user_agent or "").lower()
    return any(x in ua for x in ("mozilla/", "chrome/", "safari/", "edg/", "firefox/"))


def _block_manual_inject_browser_tbank(url: str, user_agent: Optional[str]) -> bool:
    """
    SPA mybank в браузере дергает api.*.tbank.ru / api.tinkoff.ru десятками запросов.
    url_allows_operation_inject слишком широк (payments, transfer, …) — без этого
    ручная операция вставляется в каждый похожий JSON и плодит «карточки» на главной.
    BANK_WEB_INJECT_ALL=1 — отключить ограничение.
    """
    if os.environ.get("BANK_WEB_INJECT_ALL", "").strip() == "1":
        return False
    if not _ua_looks_like_desktop_browser(user_agent):
        return False
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        path_q = ((p.path or "") + "?" + (p.query or "")).lower()
    except Exception:
        return False
    if not host:
        return False
    web_api = (host.startswith("api") and (host.endswith("tbank.ru") or "tinkoff" in host)) or host in (
        "www.tbank.ru",
        "tbank.ru",
    )
    if not web_api:
        return False
    if _url_is_mybank_certificates_statements_spa(url):
        return True
    if any(x in path_q for x in _BROWSER_TBANK_INJECT_PATH_OK):
        return False
    return True


def _cross_response_inject_debounce_hit(op_id: str) -> bool:
    """True = не вставлять (тот же op_id только что вставили в другом ответе)."""
    if os.environ.get("BANK_MANUAL_CROSS_DEBOUNCE", "").strip() == "0":
        return False
    now = time.monotonic()
    prev = _CROSS_RESPONSE_INJECT_MONO.get(op_id)
    if prev is not None and (now - prev) < 0.3:
        return True
    return False


def _cross_response_inject_mark(op_id: str) -> None:
    _CROSS_RESPONSE_INJECT_MONO[op_id] = time.monotonic()


def _mybank_page_kind(referer: Optional[str]) -> str:
    ref = (referer or "").lower()
    if not ref:
        return ""
    if "tbank.ru/mybank/operations" in ref:
        return "operations"
    if "tbank.ru/mybank/" in ref:
        return "mybank"
    return ""


def _request_looks_like_operations_feed(request_text: Optional[str]) -> bool:
    raw = (request_text or "").lower()
    if not raw:
        return False
    if "mybank/statements" in raw or "mybank%2fstatements" in raw:
        return False
    feed_hints = (
        "operations",
        "operation",
        "history",
        "feed",
        "transaction",
        "movement",
        "registry",
        "transfer",
        "sbp",
        "p2p",
        "me2me",
    )
    noise_hints = (
        "widget",
        "banner",
        "carousel",
        "shortcut",
        "digest",
        "recommend",
        "promo",
        "stories",
        "onboarding",
        "mainscreen",
        "dashboard",
        "miniapp",
        "cards",
        "accounts",
        "products",
        "moneybox",
        "mybank/statements",
    )
    return any(h in raw for h in feed_hints) and not any(h in raw for h in noise_hints)


def _response_looks_like_product_surface(data) -> bool:
    product_keys = {
        "availableBalance",
        "moneyAmount",
        "collectSum",
        "accountType",
        "cards",
        "cardList",
        "previewCards",
    }

    seen = set()

    def visit(node, depth: int = 0) -> bool:
        if depth > 6:
            return False
        nid = id(node)
        if nid in seen:
            return False
        seen.add(nid)
        if isinstance(node, dict):
            if any(k in node for k in product_keys):
                return True
            for value in node.values():
                if isinstance(value, (dict, list)) and visit(value, depth + 1):
                    return True
        elif isinstance(node, list):
            for item in node[:50]:
                if isinstance(item, (dict, list)) and visit(item, depth + 1):
                    return True
        return False

    return visit(data)


MANUAL_OPS_FILE = os.path.join(os.path.dirname(__file__), "manual_operations.json")

def load_manual_operations():
    global manual_operations, _manual_ops_mtime
    if os.path.exists(MANUAL_OPS_FILE):
        try:
            with open(MANUAL_OPS_FILE, "r", encoding="utf-8") as f:
                manual_operations = json.load(f)
                if not isinstance(manual_operations, dict):
                    manual_operations = {}
            # Backward-compat: старые записи без отдельных полей реквизитов
            # должны продолжать корректно патчить detail-экран.
            for op in manual_operations.values():
                if not isinstance(op, dict):
                    continue
                if "requisite_phone" not in op:
                    op["requisite_phone"] = (op.get("phone") or "").strip()
                if "requisite_sender_name" not in op:
                    op["requisite_sender_name"] = (op.get("sender_name") or "").strip()
            _manual_ops_mtime = os.path.getmtime(MANUAL_OPS_FILE)
            print(f"[history] Загружено {len(manual_operations)} ручных операций")
        except Exception as e:
            print(f"[history] Ошибка загрузки manual_operations: {e}")
            manual_operations = {}
            _manual_ops_mtime = None
    else:
        manual_operations = {}
        _manual_ops_mtime = None

def save_manual_operations():
    global _manual_ops_mtime
    try:
        with open(MANUAL_OPS_FILE, "w", encoding="utf-8") as f:
            json.dump(manual_operations, f, ensure_ascii=False, indent=2)
        _manual_ops_mtime = os.path.getmtime(MANUAL_OPS_FILE)
    except Exception as e:
        print(f"[history] Ошибка сохранения manual_operations: {e}")

def ensure_manual_operations_fresh():
    global _manual_ops_mtime
    try:
        current_mtime = os.path.getmtime(MANUAL_OPS_FILE) if os.path.exists(MANUAL_OPS_FILE) else None
    except OSError:
        current_mtime = None
    if current_mtime != _manual_ops_mtime:
        load_manual_operations()

load_manual_operations()

OPERATION_SAMPLE_FILE = os.path.join(os.path.dirname(__file__), "operation_sample.json")
BANK_PRESETS_FILE = os.path.join(os.path.dirname(__file__), "bank_merchants_presets.json")
_fallback_template_cache = None
_merchant_presets_cache = None
_merchant_presets_mtime = None


def _deep_merge_dict(base, over):
    out = copy.deepcopy(base) if base is not None else {}
    if not isinstance(over, dict):
        return out
    for k, v in over.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_merchant_presets():
    global _merchant_presets_cache, _merchant_presets_mtime
    try:
        mtime = os.path.getmtime(BANK_PRESETS_FILE) if os.path.exists(BANK_PRESETS_FILE) else -1
    except OSError:
        mtime = -1
    if _merchant_presets_cache is not None and mtime == _merchant_presets_mtime:
        return _merchant_presets_cache
    _merchant_presets_mtime = mtime
    if not os.path.exists(BANK_PRESETS_FILE):
        _merchant_presets_cache = {}
        return _merchant_presets_cache
    try:
        with open(BANK_PRESETS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            raw.pop("_comment", None)
        _merchant_presets_cache = raw if isinstance(raw, dict) else {}
    except Exception as e:
        print(f"[history] bank_merchants_presets.json: {e}")
        _merchant_presets_cache = {}
    return _merchant_presets_cache


def load_fallback_operation_template():
    """Шаблон операции, если в ответе пустой список (и для полей вроде logo)."""
    global _fallback_template_cache
    if _fallback_template_cache is not None:
        return _fallback_template_cache
    if not os.path.exists(OPERATION_SAMPLE_FILE):
        _fallback_template_cache = False
        return None
    try:
        with open(OPERATION_SAMPLE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            raw.pop("_comment", None)
        _fallback_template_cache = raw if isinstance(raw, dict) else False
    except Exception:
        _fallback_template_cache = False
    return _fallback_template_cache if _fallback_template_cache else None


def url_allows_operation_inject(url: str) -> bool:
    """Не трогаем гистограммы, категории и служебные API — только похожие на ленту операций."""
    u = (url or "").lower()
    if _url_is_mybank_certificates_statements_spa(u):
        return False
    # Важно: не отрезаем «graphql» целиком — у Т‑Банка лента операций часто идёт через GraphQL JSON.
    for bad in (
        "histogram",
        "category_list",
        "web-gateway",
        "log/collect",
        "providers/find",
        "session_status",
        "/event",
        "bundles",
        "ping",
    ):
        if bad in u:
            return False
    # Только явно аналитические graphql-запросы
    if "graphql" in u and any(a in u for a in ("histogram", "category_list", "analytics", "telemetry")):
        return False
    if "operations" in u or "/operation" in u or "operation/" in u:
        return True
    for good in (
        "history",
        "feed",
        "transaction",
        "payments",
        "money-session",
        "aggregated",
        "light_ib",
        "receipt",
        "cashback",
        "transfer",
        "sbp",
        "p2p",
        "me2me",
        "phone-transfer",
        "phonetransfer",
        "/statement/",
        "statement?",
        "=statement",
        "&statement",
        "movement",
        "movements",
        "registry",
        "extract",
        "certificate",
        "reference",
        "fiscal",
        "ofd",
        "slip",
        "invoice",
        "document",
    ):
        if good in u:
            return True
    # Лента часто ходит на endpoint вида .../graphql без слова operations в URL
    if "graphql" in u or "gw/graphql" in u:
        if any(b in u for b in ("histogram", "category_list", "analytics", "telemetry")):
            return False
        return True
    return False


def _graphql_manual_inject_noise_request(url: str, request_text: Optional[str]) -> bool:
    """
    Главная /mybank тянет несколько GraphQL-запросов (виджеты, карточки счетов).
    На каждый ответ срабатывала вставка ручных операций — одна и та же операция
    рисовалась несколько раз. Пропускаем запросы с типичными «не ленточными» operationName.
    BANK_GRAPHQL_INJECT_ALL=1 — отключить фильтр (отладка).
    """
    if os.environ.get("BANK_GRAPHQL_INJECT_ALL", "").strip() == "1":
        return False
    u = (url or "").lower()
    if "graphql" not in u:
        return False
    raw = (request_text or "").strip()
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except Exception:
        return False
    chunks = payload if isinstance(payload, list) else [payload]
    fragments = (
        "widget",
        "banner",
        "carousel",
        "shortcut",
        "digest",
        "recommend",
        "promo",
        "stories",
        "onboarding",
        "mainscreen",
        "dashboard",
        "miniapp",
        "skeleton",
        "hint",
        "cards",
        "accounts",
        "products",
        "moneybox",
    )

    def name_noise(name: str) -> bool:
        low = (name or "").lower()
        return any(f in low for f in fragments)

    saw_name = False
    any_clean = False
    for ch in chunks:
        if not isinstance(ch, dict):
            continue
        on = ch.get("operationName")
        if not on or not isinstance(on, str):
            continue
        saw_name = True
        if not name_noise(on):
            any_clean = True
    if not saw_name:
        # Persisted query без operationName — параллельные ответы = дубли на главной.
        if _request_looks_like_operations_feed(raw):
            return False
        try:
            gh = (urlparse(url).hostname or "").lower()
        except Exception:
            gh = ""
        if gh.endswith("tbank.ru"):
            if bank_debug_enabled():
                print("[history] graphql без operationName на *.tbank.ru — пропуск ручных операций")
            return True
        return False
    return not any_clean


def pick_template_for_type(lst, typ):
    if not lst:
        return None
    for x in lst:
        if isinstance(x, dict) and operation_row_kind(x) == typ:
            return x
    x0 = lst[0]
    return x0 if isinstance(x0, dict) else None


def _apply_bank_brand_preset(out, op):
    """Иконка/merchant из bank_merchants_presets.json + имя банка из поля «свой банк»."""
    presets = load_merchant_presets()
    key = (op.get("bank_preset") or "custom").strip().lower()
    if not key:
        key = "custom"
    block = presets.get(key)
    if not isinstance(block, dict):
        block = {}
    m_over = block.get("merchant")
    if isinstance(m_over, dict) and len(m_over) > 0:
        if isinstance(out.get("merchant"), dict):
            out["merchant"] = _deep_merge_dict(out["merchant"], m_over)
        else:
            out["merchant"] = copy.deepcopy(m_over)
    for rk, rv in (block.get("root") or {}).items():
        if isinstance(rv, dict) and isinstance(out.get(rk), dict):
            out[rk] = _deep_merge_dict(out[rk], rv)
        else:
            out[rk] = copy.deepcopy(rv)
    bank = (op.get("bank") or "").strip()
    if bank and isinstance(out.get("merchant"), dict):
        out["merchant"]["name"] = bank
    elif bank and not out.get("merchant"):
        out["merchant"] = {"name": bank}


def _normalize_logo_url(raw_logo):
    """Приводим URL к формату imgproxy, который стабильно рендерится в карточке."""
    logo = (raw_logo or "").strip()
    if not logo:
        return ""
    if "imgproxy.cdn-tinkoff.ru/compressed95/" in logo:
        return logo
    # imgproxy Т‑Банка не всегда отдает внешние домены (wiki/clearbit и т.п.).
    # Для "родных" CDN используем imgproxy, для остальных оставляем прямой URL.
    if "brands-prod.cdn-tinkoff.ru/" not in logo and "brands-static.cdn-tinkoff.ru/" not in logo:
        return logo
    encoded = base64.b64encode(logo.encode("utf-8")).decode("ascii")
    return f"https://imgproxy.cdn-tinkoff.ru/compressed95/{encoded}"


def _propagate_merchant_logo(out):
    """URL логотипа продублировать в поля, которые часто читает приложение."""
    m = out.get("merchant")
    if not isinstance(m, dict):
        return
    logo = m.get("logo") or m.get("logoUrl") or m.get("image")
    if not logo:
        return
    logo = _normalize_logo_url(logo)
    m["logo"] = logo
    for alias in ("logoUrl", "image", "icon", "picture", "avatar", "favicon"):
        m[alias] = logo
    for alias in ("logo", "logoUrl", "image", "icon", "picture", "avatar", "favicon"):
        out[alias] = logo
    cp = out.get("counterparty")
    if not isinstance(cp, dict):
        cp = {}
        out["counterparty"] = cp
    for alias in ("logo", "logoUrl", "image", "icon", "picture", "avatar", "favicon"):
        cp[alias] = logo


def _set_amount_field(container, key, value):
    if key not in container:
        return
    cur = container.get(key)
    if isinstance(cur, dict):
        cur["value"] = value
        if "currency" in cur and not cur.get("currency"):
            cur["currency"] = "RUB"
    else:
        container[key] = value


def _propagate_amount_fields(out, amt, typ):
    signed = -amt if typ == "Debit" else amt
    for key in ("amount", "operationAmount", "moneyAmount", "accountAmount", "paymentAmount", "totalAmount"):
        _set_amount_field(out, key, amt)
    _set_amount_field(out, "signedAmount", signed)
    _set_amount_field(out, "debitAmount", amt if typ == "Debit" else 0)
    _set_amount_field(out, "creditAmount", amt if typ == "Credit" else 0)

    subs = out.get("subOperations")
    if isinstance(subs, list):
        for sub in subs:
            if not isinstance(sub, dict):
                continue
            for key in ("amount", "operationAmount", "moneyAmount", "accountAmount", "paymentAmount", "totalAmount"):
                _set_amount_field(sub, key, amt)
            _set_amount_field(sub, "signedAmount", signed)
            _set_amount_field(sub, "debitAmount", amt if typ == "Debit" else 0)
            _set_amount_field(sub, "creditAmount", amt if typ == "Credit" else 0)


def parse_bank_date_str_to_ms(date_str) -> Optional[int]:
    """Строка вида 20.03.2025, 14:30:00 — иначе None (без «сейчас» по умолчанию)."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{2}):(\d{2}):(\d{2})", date_str.strip())
        if m:
            d, mo, y, H, M, S = map(int, m.groups())
            dt = datetime(y, mo, d, H, M, S)
            return int(dt.timestamp() * 1000)
    except Exception:
        pass
    return None


def millis_to_bank_date_str(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y, %H:%M:%S")


def parse_iso_date_to_ms(val) -> Optional[int]:
    if not val or not isinstance(val, str):
        return None
    try:
        dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def parse_panel_datetime_iso(dt_raw) -> tuple:
    """
    Панель и <input datetime-local> шлют ISO-подобную строку, часто без секунд (…TЧЧ:ММ).
    Возвращает (date_str в формате банка, миллисекунды эпохи). Без dt_raw — текущий момент.
    """
    if not dt_raw:
        now = datetime.now()
        return now.strftime("%d.%m.%Y, %H:%M:%S"), int(now.timestamp() * 1000)
    s = str(dt_raw).strip().replace("Z", "")
    if "+" in s:
        s = s.split("+", 1)[0]
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", s):
        s = s + ":00"
    now = datetime.now()
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%d.%m.%Y, %H:%M:%S"), int(dt.timestamp() * 1000)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d.%m.%Y, %H:%M:%S"), int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return now.strftime("%d.%m.%Y, %H:%M:%S"), int(now.timestamp() * 1000)


# Явные ключи ленты (пустой массив тоже кандидат). items/payload — только если содержимое похоже на операции.
_OPERATION_LOOSE_KEYS = frozenset(
    {
        "operations",
        "historyItems",
        "transactions",
        "feedItems",
        "moneyTransfers",
        "sbpOperations",
        "transfers",
        "transferList",
        "recentTransfers",
        "outgoingTransfers",
        "incomingTransfers",
        "sbpTransfers",
        "phoneTransfers",
        "me2meTransfers",
        "nodes",
        "edges",
    }
)
_OPERATION_AMBIGUOUS_KEYS = frozenset({"items", "payload", "data"})

# Не вставлять операции в списки под «продуктовыми» ветками JSON — иначе ручная
# операция рисуется как карточка счёта/карты на главной (несколько запросов = дубли).
_INJECT_HARD_SKIP_PATH_FRAGMENTS = (
    ".cards.",
    ".cardlist.",
    ".bankcards.",
    ".debitcards.",
    ".creditcards.",
    ".accounts.",
    ".accountlist.",
    ".products.",
    ".productlist.",
    ".recipients.",
    ".beneficiaries.",
    ".templates.",
    ".favorites.",
    ".subscriptions.",
    ".moneybox.",
    ".deposits.",
    ".loans.",
    ".offers.",
)

_INJECT_WIDGET_CONTAINER_FRAGMENTS = (
    ".widgets.",
    ".blocks.",
    ".ribbons.",
    ".shortcuts.",
    ".instruments.",
    ".banners.",
    ".carousel.",
    ".highlights.",
)


def _inject_path_unwanted(ancestors: tuple, key: str, allow_widget_containers: bool = False) -> bool:
    parts = [str(x).lower() for x in ancestors] + [str(key or "").lower()]
    dotted = "." + ".".join(parts) + "."
    if any(frag in dotted for frag in _INJECT_HARD_SKIP_PATH_FRAGMENTS):
        return True
    if not allow_widget_containers and any(frag in dotted for frag in _INJECT_WIDGET_CONTAINER_FRAGMENTS):
        return True
    return False


def _first_dict_in_list(lst):
    if not isinstance(lst, list):
        return None
    for x in lst[:30]:
        if isinstance(x, dict):
            return x
    return None


def _list_looks_like_operation_feed(lst, key: Optional[str] = None) -> bool:
    if not isinstance(lst, list) or len(lst) == 0:
        return False
    fd = _first_dict_in_list(lst)
    if not fd or not fd.get("id"):
        return False
    if operation_row_kind(fd):
        return True
    if isinstance(fd.get("operationTime"), dict) and isinstance(fd.get("amount"), dict):
        return True
    return False


def _list_looks_like_transfer_feed(lst, key: Optional[str] = None) -> bool:
    """Вкладка «Переводы» / СБП: часто без type Credit/Debit, но есть сумма и id/operationId."""
    if not isinstance(lst, list) or len(lst) == 0:
        return False
    fd = _first_dict_in_list(lst)
    if not fd:
        return False
    oid = fd.get("id") or fd.get("operationId")
    if not oid:
        return False
    if operation_row_kind(fd):
        return True
    amt = fd.get("amount")
    if not isinstance(amt, dict) or amt.get("value") is None:
        return False
    if fd.get("direction") in ("OUT", "IN", "OUTGOING", "INCOMING", "out", "in"):
        return True
    if isinstance(fd.get("pointer"), str) or isinstance(fd.get("recipientPhone"), str):
        return True
    if fd.get("status") is not None and (fd.get("bank") or fd.get("bankName") or fd.get("provider")):
        return True
    k = (key or "").lower()
    if "transfer" in k or "sbp" in k or "p2p" in k or "me2me" in k:
        return True
    return False


def _relay_node_is_bank_operation_row(n: dict) -> bool:
    """Отсечь виджеты «карта/счёт»: у них часто есть id + amount, но нет признаков операции."""
    if not isinstance(n, dict) or not n.get("id"):
        return False
    if operation_row_kind(n):
        return True
    if isinstance(n.get("operationTime"), dict):
        return True
    if isinstance(n.get("operationAmount"), dict) and n.get("operationType"):
        return True
    return False


def _list_is_graphql_relay_edges(lst) -> bool:
    """GraphQL Relay: [ { cursor, node: { id, amount?, ... } }, ... ] — только похожие на операции."""
    if not isinstance(lst, list) or len(lst) < 1:
        return False
    fd = lst[0]
    if not isinstance(fd, dict) or not isinstance(fd.get("node"), dict):
        return False
    return _relay_node_is_bank_operation_row(fd["node"])


def collect_operation_feed_lists(root, allow_widget_containers: bool = False) -> list:
    """Все массивы в дереве JSON, куда имеет смысл вставлять операции (корень часто не плоский)."""
    out = []
    seen_ids = set()

    def consider(lst: list, key: str, ancestors: tuple) -> None:
        if id(lst) in seen_ids:
            return
        if _inject_path_unwanted(ancestors, key, allow_widget_containers=allow_widget_containers):
            return
        key_l = (key or "").lower()
        transferish_key = any(
            t in key_l for t in ("transfer", "sbp", "p2p", "me2me", "phone", "pointer")
        )
        if key in _OPERATION_AMBIGUOUS_KEYS:
            if (
                _list_looks_like_operation_feed(lst, key)
                or (transferish_key and _list_looks_like_transfer_feed(lst, key))
                or (key == "edges" and _list_is_graphql_relay_edges(lst))
            ):
                seen_ids.add(id(lst))
                out.append(lst)
        elif key in _OPERATION_LOOSE_KEYS:
            if (
                len(lst) == 0
                or _list_looks_like_operation_feed(lst, key)
                or _list_looks_like_transfer_feed(lst, key)
            ):
                seen_ids.add(id(lst))
                out.append(lst)
        elif _list_looks_like_operation_feed(lst, key) or (
            transferish_key and _list_looks_like_transfer_feed(lst, key)
        ) or (key == "edges" and _list_is_graphql_relay_edges(lst)):
            seen_ids.add(id(lst))
            out.append(lst)

    def walk(node, ancestors: tuple = ()):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, list):
                    consider(v, k, ancestors)
                elif isinstance(v, (dict, list)):
                    walk(v, ancestors + (k,))
        elif isinstance(node, list):
            for el in node:
                if isinstance(el, (dict, list)):
                    walk(el, ancestors)

    if isinstance(root, (dict, list)):
        walk(root)
    return out


def pick_primary_operation_list(candidates: list) -> Optional[list]:
    """Одна «главная» лента: самый длинный непустой список, иначе любой подходящий пустой."""
    if not candidates:
        return None
    nonempty = [L for L in candidates if isinstance(L, list) and len(L) > 0]
    if nonempty:
        return max(nonempty, key=len)
    return candidates[0]


def operation_row_kind(x) -> Optional[str]:
    """Credit/Debit из type или operationType."""
    if not isinstance(x, dict):
        return None
    t = x.get("type") or x.get("operationType")
    if t in ("Credit", "Debit"):
        return t
    direction = str(x.get("direction") or "").upper()
    if direction in ("IN", "INCOMING", "CREDIT"):
        return "Credit"
    if direction in ("OUT", "OUTGOING", "DEBIT"):
        return "Debit"
    signed = x.get("signedAmount")
    if isinstance(signed, dict):
        signed = signed.get("value")
    if isinstance(signed, (int, float)):
        if signed < 0:
            return "Debit"
        if signed > 0:
            return "Credit"
    return None


def feed_sort_time_descending() -> bool:
    """True — в массиве сначала операции с большим временем (новые сверху в списке).
    False — сначала старые (новые в конце), как при history.sort_direction \"asc\" в config.json.
    Перекрывается env BANK_FEED_SORT (asc|old_first → False, desc|new_first → True)."""
    env = os.environ.get("BANK_FEED_SORT", "").strip().lower()
    if env in ("asc", "ascending", "old_first", "reverse", "1"):
        return False
    if env in ("desc", "descending", "new_first", "0"):
        return True
    try:
        return controller.config.get("history", {}).get("sort_direction", "desc") != "asc"
    except Exception:
        return True


def operation_time_ms(op):
    """Миллисекунды для сортировки (разные варианты полей в ответах Т‑Банка)."""
    if not isinstance(op, dict):
        return 0
    best = 0
    ot = op.get("operationTime")
    if isinstance(ot, dict):
        v = ot.get("milliseconds")
        if isinstance(v, (int, float)):
            best = max(best, int(v))
        v = ot.get("seconds")
        if isinstance(v, (int, float)):
            best = max(best, int(v * 1000))
    for blk in (op.get("debitingTime"), op.get("creditingTime")):
        if isinstance(blk, dict):
            v = blk.get("milliseconds")
            if isinstance(v, (int, float)):
                best = max(best, int(v))
            v = blk.get("seconds")
            if isinstance(v, (int, float)):
                best = max(best, int(v * 1000))
    if best > 0:
        return best
    for k in ("operationTimestamp", "timestamp", "time", "dateTime"):
        v = op.get(k)
        if isinstance(v, (int, float)) and v > 0:
            if v > 1e12:
                return int(v)
            if v > 1e9:
                return int(v * 1000)
    parsed = parse_bank_date_str_to_ms(op.get("date"))
    if parsed is not None:
        return parsed
    for sk in ("date", "dateTime", "datetime"):
        p = parse_iso_date_to_ms(op.get(sk))
        if p is not None:
            return p
    return 0


def max_operation_time_ms(lst):
    m = 0
    if not isinstance(lst, list):
        return m
    for x in lst:
        m = max(m, operation_time_ms(x))
    return m


def sort_operations_newest_first(lst):
    """Сортируем только строки Credit/Debit; новые должны быть сверху."""
    if not isinstance(lst, list) or len(lst) < 2:
        return
    idxs = [i for i, x in enumerate(lst) if isinstance(x, dict) and operation_row_kind(x)]
    if len(idxs) < 2:
        return
    chunk = [lst[i] for i in idxs]

    def sort_key(op):
        oid = str(op.get("id") or "")
        manual_tie = 1 if oid.startswith("m_") else 0
        return (operation_time_ms(op), manual_tie)

    chunk.sort(key=sort_key, reverse=True)
    for j, i in enumerate(idxs):
        lst[i] = chunk[j]


def _sync_all_operation_times(out: dict, ms: int) -> None:
    """Все поля времени из шаблона + milliseconds/seconds — клиент может сортировать не только по ms."""
    sec = ms / 1000.0
    if isinstance(out.get("operationTime"), dict):
        ot = out["operationTime"]
        ot["milliseconds"] = ms
        if "millis" in ot:
            ot["millis"] = ms
        ot["seconds"] = sec
        if "nanos" in ot:
            ot["nanos"] = int((ms % 1000) * 1_000_000)
    else:
        out["operationTime"] = {"milliseconds": ms, "seconds": sec}

    for tk in ("operationTimestamp", "timestamp"):
        if tk in out:
            out[tk] = ms

    dstr = millis_to_bank_date_str(ms)
    if "date" in out:
        out["date"] = dstr
    if "dateTime" in out:
        if isinstance(out["dateTime"], (int, float)):
            out["dateTime"] = ms
        elif isinstance(out["dateTime"], str):
            try:
                out["dateTime"] = datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass
    if "time" in out and isinstance(out["time"], (int, float)):
        out["time"] = ms


def _apply_manual_transfer_feed_style(out: dict, typ: str, ms: int) -> None:
    """Оформляем ручную операцию как межбанковский перевод, а не покупку/пополнение."""
    out["mcc"] = 0
    out["mccString"] = "0000"
    if "hasShoppingReceipt" in out:
        out["hasShoppingReceipt"] = False
    if "virtualPaymentType" in out:
        out["virtualPaymentType"] = 0
    out["group"] = "TRANSFER"
    out["subgroup"] = {"id": "F1", "name": "Переводы"}
    out["spendingCategory"] = {
        "id": "24",
        "name": "Переводы",
        "icon": "transfers-c1",
        "baseColor": "4FC5DF",
    }
    out["category"] = {"id": "45", "name": "Другое"}
    out["categoryInfo"] = {
        "bankCategory": {
            "id": "24",
            "language": "ru",
            "name": "Переводы",
            "baseColor": "4FC5DF",
            "fileLink": "https://brands-prod.cdn-tinkoff.ru/general_logo/transfers-c1.png",
        },
        "metacategory": {
            "id": "12",
            "language": "ru",
            "name": "Финансы",
            "baseColor": "14B8AF",
            "fileLink": "https://bms-logo-prod.t-static.ru/general_logo/finance-3-meta.png",
        },
        "criteria": {
            "bulkVariety": {
                "type": "Description",
                "value": (out.get("description") or out.get("name") or "")[:200],
            }
        },
    }
    out["additionalInfo"] = [
        {
            "fieldName": "Тип перевода",
            "fieldValue": "Перевод в другой банк" if typ == "Debit" else "Перевод из другого банка",
        }
    ]
    out["isInner"] = False
    tblock = {"milliseconds": ms}
    if typ == "Credit":
        out["creditingTime"] = dict(tblock)
        out.pop("debitingTime", None)
    else:
        out["debitingTime"] = dict(tblock)
        out.pop("creditingTime", None)


def overlay_manual_on_template(
    template,
    op_id,
    op,
    min_time_ms: Optional[int] = None,
    clamp_to_wall_ms: bool = True,
):
    """Копия реальной операции + свои строки, сумма, время, пресет банка.
    clamp_to_wall_ms=False — для экрана деталей/чека: оставляем время из панели без поджатия к «сейчас»."""
    out = copy.deepcopy(template)
    out["id"] = op_id
    typ = op.get("type") or "Debit"
    out["type"] = typ
    if "operationType" in out:
        out["operationType"] = typ
    amt = abs(float(op.get("amount") or 0))
    user_desc = (op.get("description") or "").strip()
    phone = (op.get("requisite_phone") or op.get("phone") or "").strip()
    manual_card_mask = (op.get("card_number") or "").strip()
    # Первая строка — в приоритете имя из панели; телефон только если имени нет
    primary = (op.get("title") or "").strip() or phone or user_desc
    if not primary:
        primary = "Операция" if typ == "Debit" else "Поступление"
    secondary = (op.get("subtitle") or "").strip()
    wall_ms = int(datetime.now().timestamp() * 1000)
    parsed_bank = parse_bank_date_str_to_ms(op.get("date"))
    ot_manual = op.get("operationTime") if isinstance(op.get("operationTime"), dict) else None
    picked_ms = None
    if parsed_bank is not None:
        picked_ms = parsed_bank
    elif isinstance(ot_manual, dict):
        v = ot_manual.get("milliseconds")
        if isinstance(v, (int, float)) and int(v) > 0:
            picked_ms = int(v)
    if picked_ms is not None:
        ms = picked_ms
    else:
        ms = date_str_to_millis(op.get("date", ""))
        if min_time_ms is not None:
            ms = max(ms, min_time_ms + 1)
        if clamp_to_wall_ms:
            ms = max(ms, wall_ms)

    if isinstance(out.get("amount"), dict):
        out["amount"]["value"] = amt
        if "currency" in out["amount"] and not out["amount"].get("currency"):
            out["amount"]["currency"] = "RUB"
    else:
        out["amount"] = {"value": amt, "currency": "RUB"}
    _propagate_amount_fields(out, amt, typ)

    if "name" in out:
        out["name"] = primary
    if "title" in out:
        out["title"] = primary
    merchant_name = (op.get("bank") or "").strip() or primary
    if "merchantName" in out:
        out["merchantName"] = merchant_name

    # Вторая строка / комментарий: только если пользователь ввёл; без текста по умолчанию
    second_line = secondary if secondary else (user_desc if typ == "Debit" else "")
    for key in ("description",):
        if key in out:
            out[key] = primary
    for key in ("formattedDescription",):
        if key in out:
            out[key] = second_line
    if "subtitle" in out:
        out["subtitle"] = secondary or second_line

    _sync_all_operation_times(out, ms)

    _apply_bank_brand_preset(out, op)
    _propagate_merchant_logo(out)
    _apply_manual_transfer_feed_style(out, typ, ms)
    if isinstance(out.get("additionalInfo"), list):
        if phone:
            out["additionalInfo"].append({"fieldName": "Телефон", "fieldValue": phone})
        if manual_card_mask:
            out["additionalInfo"].append({"fieldName": "Номер карты", "fieldValue": manual_card_mask})

    bal_cfg = (controller.config.get("balance") or {}) if hasattr(controller, "config") else {}
    card_mask = manual_card_mask or str(bal_cfg.get("new_card_number") or "").strip()
    if not card_mask:
        card_mask = "220070******0000"

    if "status" not in out or not out.get("status"):
        out["status"] = "OK"
    if "operationId" not in out:
        out["operationId"] = {"value": op_id, "source": "PrimeAuth"}
    if "authorizationId" not in out or not out.get("authorizationId"):
        out["authorizationId"] = op_id
    if "idSourceType" not in out:
        out["idSourceType"] = "Prime"
    # Важно: `ucid` должен соответствовать реальной карте пользователя, иначе
    # `card_credentials` вернет ошибку и блок requisites покажет empty/no-block.
    # Поэтому не перетираем эти поля без необходимости — только добиваем, если
    # их нет/они пустые, а cardNumber подставляем маской из конфига.
    if "account" not in out or not out.get("account"):
        out["account"] = "5860068322"
    if "card" not in out or not out.get("card"):
        out["card"] = "383947501"
    if "ucid" not in out or not out.get("ucid"):
        out["ucid"] = "1386102627"
    for card_key in ("cardNumber", "pan", "card_number"):
        if card_key in out or card_key == "cardNumber":
            out[card_key] = card_mask
    for phone_key in ("phone", "phoneNumber", "recipientPhone", "pointer"):
        if phone_key in out and phone:
            out[phone_key] = phone
    if isinstance(out.get("counterparty"), dict):
        cp = out["counterparty"]
        if phone:
            for phone_key in ("phone", "phoneNumber", "recipientPhone", "pointer"):
                cp[phone_key] = phone
        if card_mask:
            for card_key in ("cardNumber", "pan", "card_number"):
                cp[card_key] = card_mask
    if "accountAmount" not in out:
        out["accountAmount"] = {"value": amt, "currency": "RUB"}
    if "cashback" not in out:
        out["cashback"] = 0.0
    if "cashbackAmount" not in out:
        out["cashbackAmount"] = {"value": 0.0, "currency": "RUB"}
    if "locations" not in out:
        out["locations"] = []
    if "senderDetails" not in out:
        out["senderDetails"] = ""
    if "subcategory" not in out or not out.get("subcategory"):
        out["subcategory"] = secondary or primary
    if "loyaltyBonus" not in out:
        out["loyaltyBonus"] = []
    if "loyaltyPayment" not in out:
        out["loyaltyPayment"] = []
    if "loyaltyBonusSummary" not in out:
        out["loyaltyBonusSummary"] = {"amount": 0.0}
    if "offers" not in out:
        out["offers"] = []
    if "cardPresent" not in out:
        out["cardPresent"] = False
    if "isExternalCard" not in out:
        out["isExternalCard"] = False
    if "isHce" not in out:
        out["isHce"] = False
    if "isSuspicious" not in out:
        out["isSuspicious"] = False
    if "hasStatement" not in out:
        out["hasStatement"] = True
    if "isDispute" not in out:
        out["isDispute"] = False
    if "operationTransferred" not in out:
        out["operationTransferred"] = False
    if "isOffline" not in out:
        out["isOffline"] = False
    if "analyticsStatus" not in out:
        out["analyticsStatus"] = "NotSpecified"
    if "isTemplatable" not in out:
        out["isTemplatable"] = False
    if "trancheCreationAllowed" not in out:
        out["trancheCreationAllowed"] = False
    if "merchantKey" not in out:
        out["merchantKey"] = f"MANUAL_{op_id}"
    if "posId" not in out:
        out["posId"] = "585"
    if "typeSerno" not in out:
        out["typeSerno"] = 151
    if "tags" not in out:
        out["tags"] = []
    if "isAuto" not in out:
        out["isAuto"] = False
    if "merges" not in out:
        out["merges"] = []
    if "documents" not in out:
        out["documents"] = ["Statement"]

    merchant = out.get("merchant") if isinstance(out.get("merchant"), dict) else {}
    brand_logo = ""
    brand_name = merchant.get("name") if isinstance(merchant, dict) else ""
    # Проверяем логотип из ручной операции
    manual_logo = op.get("logo") or ""
    for key in ("logo", "logoUrl", "image", "icon", "picture", "avatar", "favicon"):
        if isinstance(merchant, dict) and merchant.get(key):
            brand_logo = merchant[key]
            break
    # Если есть логотип в ручной операции - используем его
    if manual_logo:
        brand_logo = manual_logo
    if "brand" not in out or not isinstance(out.get("brand"), dict):
        out["brand"] = {
            "id": "11250",
            "name": brand_name or merchant_name or primary,
            "logo": brand_logo,
            "baseColor": "f12e16",
            "fileLink": brand_logo,
        }
    else:
        if brand_name and not out["brand"].get("name"):
            out["brand"]["name"] = brand_name
        if brand_logo:
            for key in ("logo", "fileLink"):
                if not out["brand"].get(key):
                    out["brand"][key] = brand_logo

    if "counterparty" in out and isinstance(out["counterparty"], dict):
        bn = (op.get("bank") or "").strip()
        if bn:
            out["counterparty"]["name"] = bn
        elif primary and not out["counterparty"].get("name"):
            out["counterparty"]["name"] = primary

    if "parentOperationId" in out:
        out["parentOperationId"] = None

    return out


def date_str_to_millis(date_str):
    try:
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{2}):(\d{2}):(\d{2})", date_str or "")
        if m:
            d, mo, y, H, M, S = map(int, m.groups())
            dt = datetime(y, mo, d, H, M, S)
            return int(dt.timestamp() * 1000)
    except Exception:
        pass
    return int(datetime.now().timestamp() * 1000)


def compute_manual_transfer_month_totals() -> tuple:
    """Суммы ручных «переводов в/из банков» за текущий месяц: исходящие (Debit), входящие (Credit)."""
    ensure_manual_operations_fresh()
    out_sum = in_sum = 0.0
    for oid, op in manual_operations.items():
        if oid in hidden_operations:
            continue
        if not is_current_month(op.get("date", "")):
            continue
        amt = float(op.get("amount") or 0)
        if op.get("type") == "Debit":
            out_sum += abs(amt)
        elif op.get("type") == "Credit":
            in_sum += amt
    return round(out_sum, 2), round(in_sum, 2)


def apply_manual_transfer_summary_adjustments(data, url: str) -> bool:
    """
    Подкручивает типичные поля сводок на вкладке переводов (исходящие/входящие за период).
    Вызывать только для URL с transfer/sbp/p2p/me2me.
    """
    u = (url or "").lower()
    if not any(x in u for x in ("transfer", "sbp", "p2p", "me2me")):
        return False
    if any(b in u for b in ("histogram", "category_list", "graphql")):
        return False
    if any(b in u for b in ("payment_receipt", "get_requisites", "payment_commission", "receipt_pdf")):
        return False
    if any(b in u for b in ("operationbyid", "operation/info", "operation_detail")):
        return False
    out_add, in_add = compute_manual_transfer_month_totals()
    if out_add == 0 and in_add == 0:
        return False

    skip_key_fragments = ("limit", "fee", "commission", "min", "max", "rate", "percent", "cashback")

    def bump_key(lk: str) -> Optional[str]:
        if any(s in lk for s in skip_key_fragments):
            return None
        if out_add and any(
            f in lk
            for f in (
                "outgoing",
                "outbound",
                "sent",
                "debit",
                "outflow",
                "исход",
                "отправ",
            )
        ):
            if not any(b in lk for b in ("incoming", "received", "inbound", "вход")):
                return "out"
        if in_add and any(
            f in lk
            for f in (
                "incoming",
                "inbound",
                "received",
                "credit",
                "inflow",
                "вход",
                "поступ",
            )
        ):
            if not any(b in lk for b in ("outgoing", "outbound", "sent", "debit", "исход")):
                return "in"
        return None

    changed = False

    def visit(node):
        nonlocal changed
        if isinstance(node, dict):
            for k, v in list(node.items()):
                lk = k.lower()
                kind = bump_key(lk)
                if kind and isinstance(v, dict) and isinstance(v.get("value"), (int, float)):
                    moneyish = "currency" in v or any(
                        x in lk
                        for x in (
                            "amount",
                            "sum",
                            "total",
                            "outgoing",
                            "incoming",
                            "aggregate",
                            "summary",
                            "spent",
                            "earned",
                        )
                    )
                    if moneyish:
                        add = out_add if kind == "out" else in_add
                        v["value"] = round(float(v["value"]) + add, 2)
                        changed = True
                        continue
                visit(v)
        elif isinstance(node, list):
            for x in node:
                visit(x)

    visit(data)
    return changed


def _candidate_operation_lists_from_data(
    data,
    allow_graphql_edges: bool = True,
    allow_widget_containers: bool = False,
) -> list:
    out = []
    seen = set()

    def add_list(lst):
        if not isinstance(lst, list):
            return
        lid = id(lst)
        if lid in seen:
            return
        seen.add(lid)
        out.append(lst)

    if isinstance(data, list):
        if (
            len(data) == 0
            or _list_looks_like_operation_feed(data)
            or _list_looks_like_transfer_feed(data)
            or (allow_graphql_edges and _list_is_graphql_relay_edges(data))
        ):
            add_list(data)

    if isinstance(data, (dict, list)):
        for lst in collect_operation_feed_lists(
            data,
            allow_widget_containers=allow_widget_containers,
        ):
            if not allow_graphql_edges and _list_is_graphql_relay_edges(lst):
                continue
            add_list(lst)

    return out


def inject_manual_into_response(
    data,
    url: str,
    request_text: Optional[str] = None,
    user_agent: Optional[str] = None,
    referer: Optional[str] = None,
) -> bool:
    """Вставка ручных операций по образцу реальных из того же ответа. Возвращает True, если JSON менялся."""
    ensure_manual_operations_fresh()
    if not manual_operations:
        return False
    if not isinstance(data, (dict, list)):
        return False
    if url_prohibit_proxy_json_mutation(url):
        return False
    if _flow_is_statements_certificates_context(url, referer, request_text):
        return False
    page_kind = _mybank_page_kind(referer)
    if _ua_looks_like_desktop_browser(user_agent):
        return False
    request_feed_like = _request_looks_like_operations_feed(request_text)
    product_surface = _response_looks_like_product_surface(data)
    if page_kind == "" and request_feed_like and not product_surface:
        page_kind = "operations"
    candidates = _candidate_operation_lists_from_data(
        data,
        allow_graphql_edges=(page_kind != "mybank"),
        allow_widget_containers=(page_kind == "operations" and not product_surface),
    )
    has_operation_candidates = bool(candidates)
    if product_surface and page_kind != "operations":
        if bank_debug_enabled():
            print(f"[history] ручные операции: пропуск product-surface ответа: {url[:140]}")
        return False
    if not url_allows_operation_inject(url) and not has_operation_candidates:
        if bank_debug_enabled():
            print(f"[history] ручные операции: URL пропущен (не лента операций): {url[:140]}")
        return False
    if page_kind != "operations" and _block_manual_inject_browser_tbank(url, user_agent) and not has_operation_candidates:
        if bank_debug_enabled():
            print(f"[history] ручные операции: браузер + веб-API, путь не похож на ленту — пропуск: {url[:140]}")
        return False
    if page_kind != "operations" and _graphql_manual_inject_noise_request(url, request_text) and not has_operation_candidates:
        if bank_debug_enabled():
            print(f"[history] ручные операции: GraphQL operationName — виджет/шум, пропуск: {url[:120]}")
        return False

    pending = []
    for op_id, op in manual_operations.items():
        if op_id in hidden_operations:
            continue
        if not is_current_month(op.get("date", "")):
            continue
        pending.append((op_id, op))
    if not pending:
        if manual_operations and bank_debug_enabled():
            print(
                "[history] ручные операции есть, но ни одна не в текущем месяце — "
                "проверьте дату в панели (формат ДД.ММ.ГГГГ в текущем месяце)."
            )
        return False

    changed = False
    injected_ids = set()
    use_cross_debounce = _ua_looks_like_desktop_browser(user_agent)
    url_u = (url or "").lower()
    # Не использовать подстроку "transfer" — она есть почти везде; иначе multi_transfer
    # включается, share_injected_ids=False и одна ручная операция дублируется во все списки ответа.
    multi_transfer = any(
        x in url_u
        for x in (
            "phone-transfer",
            "phonetransfer",
            "money-transfer",
            "internal-transfer",
            "/transfers/",
            "transferlist",
            "transfer-list",
            "outgoingtransfer",
            "incomingtransfer",
            "sbp-transfer",
            "/sbp/",
            "p2p",
            "me2me",
            "me-to-me",
        )
    )

    def merge_into_list(lst, share_injected_ids: bool = True):
        nonlocal changed
        if not isinstance(lst, list):
            return
        if _list_is_graphql_relay_edges(lst):
            nodes_list = [
                x["node"]
                for x in lst
                if isinstance(x, dict) and isinstance(x.get("node"), dict)
            ]
            existing_ids = {n.get("id") for n in nodes_list if isinstance(n, dict)}
            fallback_tpl = load_fallback_operation_template()
            tick_ms = max((operation_time_ms(n) for n in nodes_list), default=0)

            for op_id, op in pending:
                if op_id in existing_ids:
                    continue
                if share_injected_ids and op_id in injected_ids:
                    continue
                if use_cross_debounce and _cross_response_inject_debounce_hit(op_id):
                    continue
                typ = op.get("type") or "Debit"
                template = pick_template_for_type(nodes_list, typ) if nodes_list else None
                if template is None:
                    template = fallback_tpl
                if template is None:
                    if bank_debug_enabled():
                        print(f"[history] нет шаблона (GraphQL edges), id={op_id[:16]}")
                    continue
                try:
                    item = overlay_manual_on_template(
                        copy.deepcopy(template), op_id, op, min_time_ms=tick_ms
                    )
                    ot = item.get("operationTime") if isinstance(item, dict) else None
                    if isinstance(ot, dict) and isinstance(ot.get("milliseconds"), (int, float)):
                        tick_ms = int(ot["milliseconds"])
                except Exception as ex:
                    print(f"[history] overlay операции (edges): {ex}")
                    continue
                lst.insert(0, {"cursor": f"m_{op_id}", "node": item})
                existing_ids.add(op_id)
                if share_injected_ids:
                    injected_ids.add(op_id)
                if use_cross_debounce:
                    _cross_response_inject_mark(op_id)
                changed = True

            def relay_sk(e):
                if not isinstance(e, dict) or not isinstance(e.get("node"), dict):
                    return (0, 0)
                oid = str(e["node"].get("id") or "")
                manual_tie = 1 if oid.startswith("m_") else 0
                return (operation_time_ms(e["node"]), manual_tie)

            lst.sort(key=relay_sk, reverse=True)
            if bank_debug_enabled() and changed:
                print(f"[history] inject GraphQL edges: count={len(lst)}")
            return

        existing_ids = {x.get("id") for x in lst if isinstance(x, dict)}
        fallback_tpl = load_fallback_operation_template()
        tick_ms = max_operation_time_ms(lst)

        for op_id, op in pending:
            if op_id in existing_ids:
                continue
            if share_injected_ids and op_id in injected_ids:
                continue
            if use_cross_debounce and _cross_response_inject_debounce_hit(op_id):
                continue
            typ = op.get("type") or "Debit"
            template = pick_template_for_type(lst, typ)
            if template is None:
                template = fallback_tpl
            if template is None:
                if bank_debug_enabled():
                    print(f"[history] нет шаблона операции (пустой список и нет operation_sample.json), id={op_id[:16]}")
                continue
            try:
                item = overlay_manual_on_template(template, op_id, op, min_time_ms=tick_ms)
                ot = item.get("operationTime") if isinstance(item, dict) else None
                if isinstance(ot, dict) and isinstance(ot.get("milliseconds"), (int, float)):
                    tick_ms = int(ot["milliseconds"])
            except Exception as ex:
                print(f"[history] overlay операции: {ex}")
                continue
            lst.insert(0, item)
            existing_ids.add(op_id)
            if share_injected_ids:
                injected_ids.add(op_id)
            if use_cross_debounce:
                _cross_response_inject_mark(op_id)
            changed = True

        sort_operations_newest_first(lst)
        if bank_debug_enabled() and changed and lst:
            ts = [operation_time_ms(x) for x in lst if isinstance(x, dict) and operation_row_kind(x)]
            if ts:
                print(
                    f"[history] inject: len={len(lst)} time_ms min={min(ts)} max={max(ts)} "
                    f"first_id={lst[0].get('id') if isinstance(lst[0], dict) else '?'}"
                )

    if isinstance(data, list):
        cand = candidates
        primary = pick_primary_operation_list(cand)
        if primary is not None:
            merge_into_list(primary, share_injected_ids=not multi_transfer)
        if apply_manual_transfer_summary_adjustments(data, url):
            changed = True
        return changed

    if multi_transfer and candidates:
        for lst in candidates:
            merge_into_list(lst, share_injected_ids=False)
    else:
        primary = pick_primary_operation_list(candidates)
        if primary is not None:
            merge_into_list(primary, share_injected_ids=True)
        else:
            for field in ("payload", "items", "operations"):
                if field in data and isinstance(data[field], list):
                    merge_into_list(data[field], share_injected_ids=True)
            for key, val in data.items():
                if key in ("payload", "items", "operations"):
                    continue
                if (
                    isinstance(val, list)
                    and len(val) > 0
                    and isinstance(val[0], dict)
                    and val[0].get("id")
                    and operation_row_kind(val[0])
                ):
                    merge_into_list(val, share_injected_ids=True)

    if apply_manual_transfer_summary_adjustments(data, url):
        changed = True
    if changed:
        print(f"[history] ✓ подменён ответ (ручные операции): {url[:130]}")
    return changed


def _list_is_operation_like(lst) -> bool:
    """Не считать «операциями» списки только с id+amount (например заказы справок)."""
    if not isinstance(lst, list) or not lst:
        return False
    n = 0
    for x in lst[:8]:
        if not isinstance(x, dict) or not x.get("id"):
            continue
        if operation_row_kind(x):
            n += 1
        elif isinstance(x.get("operationTime"), dict) and isinstance(x.get("amount"), dict):
            n += 1
    return n > 0


def apply_hidden_operations_filter(
    data, url: str = "", referer: Optional[str] = None, request_text: Optional[str] = None
) -> bool:
    """Рекурсивно убираем скрытые id из любых списков операций в JSON."""
    if not hidden_operations:
        return False
    if not isinstance(data, (dict, list)):
        return False
    if url_prohibit_proxy_json_mutation(url):
        return False
    if _flow_is_statements_certificates_context(url, referer, request_text):
        return False
    modified = False

    def visit(obj):
        nonlocal modified
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if isinstance(v, list) and _list_is_operation_like(v):
                    new_v = [x for x in v if not (isinstance(x, dict) and x.get("id") in hidden_operations)]
                    if len(new_v) != len(v):
                        obj[k] = new_v
                        modified = True
                    for x in obj[k]:
                        visit(x)
                else:
                    visit(v)
        elif isinstance(obj, list):
            for el in obj:
                visit(el)

    visit(data)
    if modified:
        print(f"[history] Удалены скрытые операции из ответа (рекурсивно)")
    return modified

def is_current_month(date_str):
    """Текущий календарный месяц (не «как при старте mitm»)."""
    try:
        match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", date_str or "")
        if match:
            day, month, year = map(int, match.groups())
            now = datetime.now()
            return year == now.year and month == now.month
    except Exception:
        pass
    return False


_LAST_TRANSFER_DIR = os.path.dirname(os.path.abspath(__file__))


def _last_transfer_json_paths():
    """last_transfer*.json рядом с history.py и в cwd процесса mitm — save в transfer.py мог быть относительно cwd."""
    names = ("last_transfer.json", "last_transfer2.json")
    dirs = []
    for d in (_LAST_TRANSFER_DIR, os.path.normpath(os.getcwd())):
        if d not in dirs:
            dirs.append(d)
    paths = []
    seen = set()
    for d in dirs:
        for n in names:
            p = os.path.normpath(os.path.join(d, n))
            if p in seen:
                continue
            seen.add(p)
            paths.append(p)
    return paths


def _fake_op_in_current_month(op: dict) -> bool:
    if not isinstance(op, dict):
        return False
    d = (op.get("date_full") or op.get("date") or "").strip()
    if d and is_current_month(d):
        return True
    ot = op.get("operationTime") or {}
    if isinstance(ot, dict):
        ms = ot.get("milliseconds")
        if isinstance(ms, (int, float)) and ms > 0:
            dt = datetime.fromtimestamp(ms / 1000)
            now = datetime.now()
            return dt.year == now.year and dt.month == now.month
    return False


def _iter_fake_debit_ops_month():
    """Debit из fake_history за текущий месяц: (id_str или '', amount, op)."""
    seen_ids = set()
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if bank_debug_enabled():
                print(f"[history] _iter_fake_debit_ops_month {path}: {e}")
            continue
        for op in data.get("fake_history") or []:
            if not isinstance(op, dict) or op.get("type") != "Debit":
                continue
            if not _fake_op_in_current_month(op):
                continue
            oid = op.get("id")
            oid_s = str(oid).strip() if oid is not None and str(oid).strip() else ""
            if oid_s:
                if oid_s in seen_ids or oid_s in hidden_operations:
                    continue
                seen_ids.add(oid_s)
            amt = op.get("amount")
            if isinstance(amt, dict):
                amt = amt.get("value", 0)
            yield oid_s, float(amt or 0), op


def _iter_fake_credit_ops_month():
    """Credit из fake_history за текущий месяц: (id_str или '', amount, op)."""
    seen_ids = set()
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if bank_debug_enabled():
                print(f"[history] _iter_fake_credit_ops_month {path}: {e}")
            continue
        for op in data.get("fake_history") or []:
            if not isinstance(op, dict) or op.get("type") != "Credit":
                continue
            if not _fake_op_in_current_month(op):
                continue
            oid = op.get("id")
            oid_s = str(oid).strip() if oid is not None and str(oid).strip() else ""
            if oid_s:
                if oid_s in seen_ids or oid_s in hidden_operations:
                    continue
                seen_ids.add(oid_s)
            amt = op.get("amount")
            if isinstance(amt, dict):
                amt = amt.get("value", 0)
            yield oid_s, float(amt or 0), op


def _iter_fake_debit_ops_all():
    """Все Debit из fake_history (не только текущий месяц)."""
    seen_ids = set()
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if bank_debug_enabled():
                print(f"[history] _iter_fake_debit_ops_all {path}: {e}")
            continue
        for op in data.get("fake_history") or []:
            if not isinstance(op, dict) or op.get("type") != "Debit":
                continue
            oid = op.get("id")
            oid_s = str(oid).strip() if oid is not None and str(oid).strip() else ""
            if oid_s:
                if oid_s in seen_ids or oid_s in hidden_operations:
                    continue
                seen_ids.add(oid_s)
            amt = op.get("amount")
            if isinstance(amt, dict):
                amt = amt.get("value", 0)
            yield oid_s, float(amt or 0), op


def _iter_fake_credit_ops_all():
    """Все Credit из fake_history (не только текущий месяц)."""
    seen_ids = set()
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            if bank_debug_enabled():
                print(f"[history] _iter_fake_credit_ops_all {path}: {e}")
            continue
        for op in data.get("fake_history") or []:
            if not isinstance(op, dict) or op.get("type") != "Credit":
                continue
            oid = op.get("id")
            oid_s = str(oid).strip() if oid is not None and str(oid).strip() else ""
            if oid_s:
                if oid_s in seen_ids or oid_s in hidden_operations:
                    continue
                seen_ids.add(oid_s)
            amt = op.get("amount")
            if isinstance(amt, dict):
                amt = amt.get("value", 0)
            yield oid_s, float(amt or 0), op


def _fake_credit_month_total() -> float:
    t = 0.0
    for _oid, amt, _op in _iter_fake_credit_ops_month():
        t += amt
    return round(min(t, 1e15), 2)


def _fake_debit_extra_not_in_cache_all() -> tuple:
    """Сумма и число мок‑расходов из fake_history с id, которых ещё нет в кэше/ручных (все даты)."""
    ensure_manual_operations_fresh()
    total = 0.0
    n = 0
    for oid_s, amt, _op in _iter_fake_debit_ops_all():
        if not oid_s:
            continue
        if oid_s in operations_cache or oid_s in manual_operations:
            continue
        total += amt
        n += 1
    return round(min(total, 1e15), 2), n


def get_fake_expense_from_last_transfer() -> float:
    """
    Полная сумма Debit из fake_history за месяц (для доп. к гистограмме банка:
    банк мок‑переводы в сводку не кладёт).
    """
    total = 0.0
    for _oid_s, amt, _op in _iter_fake_debit_ops_month():
        total += amt
    return round(min(total, 1e15), 2)


def get_fake_expense_not_in_operations_cache() -> float:
    """Часть fake_history, которой ещё нет в operations_cache — чтобы не дублировать в calculate_stats."""
    ensure_manual_operations_fresh()
    total = 0.0
    for oid_s, amt, _op in _iter_fake_debit_ops_month():
        if oid_s and oid_s in operations_cache:
            continue
        total += amt
    return round(min(total, 1e15), 2)


def _count_fake_debit_ops_not_in_cache() -> int:
    ensure_manual_operations_fresh()
    n = 0
    for oid_s, _amt, _op in _iter_fake_debit_ops_month():
        if oid_s and oid_s in operations_cache:
            continue
        n += 1
    return n


def _panel_transfer_expense_addon() -> float:
    """Доп. расход для строки «как у банка»: fake_history или legacy total_out_rub (как в calculate_stats)."""
    fake = get_fake_expense_from_last_transfer()
    if fake > 0:
        return fake
    return float((controller.config.get("transfers") or {}).get("total_out_rub", 0) or 0)


def _fake_bank_display_name(op: dict) -> str:
    if not isinstance(op, dict):
        return ""
    b = op.get("brand")
    if isinstance(b, dict) and b.get("name"):
        return str(b.get("name") or "")
    return get_op_bank(op) or ""


def _fake_transfer_ops_for_panel(skip_ids: set, month_only: bool = True) -> list:
    """Операции из fake_history для списка панели."""
    seen_ids = set()
    out = []
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for op in data.get("fake_history") or []:
            if not isinstance(op, dict):
                continue
            oid = str(op.get("id") or "").strip()
            if not oid or oid in skip_ids or oid in seen_ids:
                continue
            seen_ids.add(oid)
            if month_only and not _fake_op_in_current_month(op):
                continue
            if not op.get("type") or op.get("type") not in ("Debit", "Credit"):
                continue
            ot = op.get("operationTime") or {}
            ms = int(ot.get("milliseconds") or 0) if isinstance(ot, dict) else 0
            sort_ts = (10**15) + (ms if ms > 0 else int(time.time() * 1000))
            amt = get_op_amount(op)
            line1 = get_op_description(op) or _fake_bank_display_name(op) or "Перевод"
            date_s = (op.get("date_full") or "").strip() or get_op_date(op)
            rp = str(op.get("receiver_phone") or op.get("requisite_phone") or "").strip()
            rn = str(op.get("receiver_name") or op.get("requisite_sender_name") or "").strip()
            brx = str(op.get("bank_receiver") or "").strip()
            out.append({
                "id": oid,
                "date": date_s,
                "sort_ts": sort_ts,
                "amount": amt,
                "type": op.get("type"),
                "desc": line1,
                "title": op.get("title") or line1,
                "subtitle": op.get("subcategory") or "",
                "description": op.get("description") or "",
                "bank": _fake_bank_display_name(op) or brx,
                "bank_preset": "sbp",
                "phone": rp,
                "requisite_phone": rp,
                "sender_name": rn,
                "requisite_sender_name": rn,
                "card_number": str(op.get("receiver_card") or op.get("card_number") or "").strip(),
                "manual": False,
                "fake_transfer": True,
            })
    return out


def _fake_transfer_ops_for_panel_month(skip_ids: set) -> list:
    return _fake_transfer_ops_for_panel(skip_ids, month_only=True)


def op_id_in_fake_history_files(op_id: str) -> bool:
    if not op_id:
        return False
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for op in data.get("fake_history") or []:
            if isinstance(op, dict) and str(op.get("id") or "") == str(op_id):
                return True
    return False


def _fake_history_record_by_id(op_id: str) -> Optional[dict]:
    """Первая запись fake_history с данным id (для слияния с operations_cache в панели)."""
    if not op_id:
        return None
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        for op in data.get("fake_history") or []:
            if isinstance(op, dict) and str(op.get("id") or "") == str(op_id):
                return op
    return None


def _resolve_stored_receipt_pdf(stored: str) -> Optional[str]:
    """Абсолютный путь к PDF чека, если файл существует."""
    if not stored or not isinstance(stored, str):
        return None
    p = stored.strip()
    if os.path.isabs(p) and os.path.isfile(p):
        return p
    _hd = os.path.dirname(os.path.abspath(__file__))
    for base in (_hd, os.path.normpath(os.getcwd())):
        cand = os.path.normpath(os.path.join(base, p))
        if os.path.isfile(cand):
            return cand
    cand = os.path.join(_hd, os.path.basename(p))
    if os.path.isfile(cand):
        return cand
    return None


def _fake_history_op_to_receipt_dict(hop: dict) -> dict:
    """Поля для func.generate_operation_receipt из записи fake_history."""
    oid = str(hop.get("id") or "")
    amt = hop.get("amount")
    if isinstance(amt, dict):
        amt = float(amt.get("value") or 0)
    else:
        amt = abs(float(amt or 0))
    date_s = (hop.get("date_full") or "").strip()
    if not date_s and isinstance(hop.get("operationTime"), dict):
        try:
            ms = int(hop["operationTime"].get("milliseconds") or 0)
            if ms > 0:
                date_s = datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M:%S")
        except (TypeError, ValueError, OSError):
            pass
    if not date_s:
        date_s = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    bank_line = (hop.get("bank_receiver") or "").strip()
    if not bank_line:
        br = hop.get("brand")
        if isinstance(br, dict):
            bank_line = (br.get("name") or "").strip()
    if not bank_line:
        bank_line = hop.get("description") or hop.get("subcategory") or "Перевод"
    title = (hop.get("receiver_name") or hop.get("description") or "").strip() or "Получатель"
    phone = str(hop.get("receiver_phone") or "").strip()
    return {
        "id": oid,
        "date": date_s,
        "amount": amt,
        "type": hop.get("type") or "Debit",
        "bank": bank_line,
        "title": title,
        "requisite_phone": phone,
        "phone": phone,
    }


def _write_fake_op_pdf_path(op_id: str, pdf_path: str) -> None:
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        fh = data.get("fake_history")
        if not isinstance(fh, list):
            continue
        modified = False
        for op in fh:
            if isinstance(op, dict) and str(op.get("id")) == str(op_id):
                op["pdf_path"] = pdf_path
                modified = True
                break
        if modified:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                if bank_debug_enabled():
                    print(f"[history] _write_fake_op_pdf_path {path}: {e}")
            return


def ensure_operation_receipt_pdf_path(op_id: str) -> Optional[str]:
    """
    PDF-чек для ручной операции (manual_operations) или мок‑перевода (fake_history).
    Возвращает абсолютный путь к файлу или None.
    """
    if not op_id:
        return None
    ensure_manual_operations_fresh()
    op_id = str(op_id).strip()

    if op_id in manual_operations:
        op = manual_operations[op_id]
        pdf_abs = _resolve_stored_receipt_pdf(str(op.get("pdf_path") or ""))
        if pdf_abs:
            return pdf_abs
        op_data = {
            "id": op_id,
            "date": op.get("date") or "",
            "amount": abs(float(op.get("amount") or 0)),
            "type": op.get("type") or "Debit",
            "bank": op.get("bank") or (op.get("bank_preset") or "") or "Перевод",
            "title": op.get("title") or op.get("description") or "",
            "phone": op.get("requisite_phone") or op.get("phone") or "",
            "receipt_phone": op.get("receipt_phone") or "",
            "sender_name": op.get("sender_name") or "",
            "requisite_sender_name": op.get("requisite_sender_name") or op.get("sender_name") or "",
        }
        try:
            pdf_new = func.generate_operation_receipt(op_data)
        except Exception:
            return None
        if pdf_new:
            manual_operations[op_id]["pdf_path"] = pdf_new
            save_manual_operations()
            return _resolve_stored_receipt_pdf(pdf_new) or (
                pdf_new if os.path.isfile(pdf_new) else None
            )
        return None

    hop = _fake_history_record_by_id(op_id)
    if not hop:
        return None
    pdf_abs = _resolve_stored_receipt_pdf(str(hop.get("pdf_path") or ""))
    if pdf_abs:
        return pdf_abs
    try:
        pdf_new = func.generate_operation_receipt(_fake_history_op_to_receipt_dict(hop))
    except Exception:
        return None
    if pdf_new:
        _write_fake_op_pdf_path(op_id, pdf_new)
        return _resolve_stored_receipt_pdf(pdf_new) or (
            pdf_new if os.path.isfile(pdf_new) else None
        )
    return None


def remove_fake_transfer_operation(op_id: str) -> bool:
    if not op_id:
        return False
    changed = False
    for path in _last_transfer_json_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        fh = data.get("fake_history")
        if not isinstance(fh, list):
            continue
        new_fh = [x for x in fh if not (isinstance(x, dict) and str(x.get("id") or "") == str(op_id))]
        if len(new_fh) != len(fh):
            data["fake_history"] = new_fh
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                changed = True
            except Exception as e:
                if bank_debug_enabled():
                    print(f"[history] remove_fake_transfer_operation {path}: {e}")
    hidden_operations.discard(op_id)
    return changed


def extract_operations(data):
    ops = []
    seen_ids = set()

    for lst in _candidate_operation_lists_from_data(data):
        for item in lst:
            if not isinstance(item, dict):
                continue
            op = item.get("node") if isinstance(item.get("node"), dict) else item
            if not isinstance(op, dict):
                continue
            ex_id = str(op.get("id") or op.get("operationId") or "")
            if not ex_id or ex_id in seen_ids:
                continue
            if not (
                operation_row_kind(op)
                or isinstance(op.get("amount"), dict)
                or isinstance(op.get("operationAmount"), dict)
                or isinstance(op.get("accountAmount"), dict)
                or isinstance(op.get("paymentAmount"), dict)
                or isinstance(op.get("totalAmount"), dict)
            ):
                continue
            seen_ids.add(ex_id)
            ops.append(op)

    if ops:
        return ops

    candidates = []
    if isinstance(data, dict):
        for field in ['payload', 'items', 'operations']:
            if field in data and isinstance(data[field], list):
                candidates.extend(data[field])
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0:
                candidates.extend(val)
    elif isinstance(data, list):
        candidates = data
    ops = []
    for op in candidates:
        if not isinstance(op, dict):
            continue
        if not op.get('id'):
            continue
        if not op.get('type') or op.get('type') not in ['Credit', 'Debit']:
            continue
        if not op.get('amount'):
            continue
        ops.append(op)
    return ops if ops else None

def get_op_date(op):
    ts = op.get('date') or op.get('datetime') or op.get('timestamp') or op.get('operationTime', {}).get('milliseconds')
    if ts:
        if isinstance(ts, (int, float)):
            if ts > 1e10:
                dt = datetime.fromtimestamp(ts/1000)
            else:
                dt = datetime.fromtimestamp(ts)
            return dt.strftime("%d.%m.%Y, %H:%M:%S")
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                return dt.strftime("%d.%m.%Y, %H:%M:%S")
            except:
                return ts
    return datetime.now().strftime("%d.%m.%Y, %H:%M:%S")

def get_op_amount(op):
    for key in ("amount", "operationAmount", "accountAmount", "paymentAmount", "totalAmount", "signedAmount"):
        if key not in op:
            continue
        if isinstance(op[key], dict):
            return abs(float(op[key].get('value', 0)))
        return abs(float(op[key]))
    return 0.0

def get_op_type(op):
    return operation_row_kind(op) or ''

def get_op_description(op):
    return op.get('description') or op.get('name') or op.get('title') or ''

def get_op_bank(op):
    if 'merchant' in op and isinstance(op['merchant'], dict):
        return op['merchant'].get('name', '')
    if 'counterparty' in op and isinstance(op['counterparty'], dict):
        return op['counterparty'].get('name', '')
    return ''

def clean_old_ops():
    MAX_OPS = 1200
    if len(operations_cache) > MAX_OPS:
        sorted_ops = sorted(operations_cache.items(), key=lambda x: x[1].get("date", ""), reverse=True)
        operations_cache.clear()
        for k, v in sorted_ops[:MAX_OPS]:
            operations_cache[k] = v

def _manual_operations_income_expense_month(restrict_month: bool = True) -> tuple:
    """Суммы доход/расход по manual_operations.json (по умолчанию только текущий месяц)."""
    ensure_manual_operations_fresh()
    inc = exp = 0.0
    for op_id, op in manual_operations.items():
        if op_id in hidden_operations:
            continue
        if restrict_month and not is_current_month(op.get("date", "")):
            continue
        amt = float(op.get("amount") or 0)
        if op.get("type") == "Credit":
            inc += amt
        elif op.get("type") == "Debit":
            exp += abs(amt)
    return round(inc, 2), round(exp, 2)


def calculate_manual_and_mock_transfer_stats(restrict_month: bool = True):
    """
    Доходы/расходы и счётчики только для ручных операций (manual_operations.json)
    и мок‑переводов (fake_history / legacy total_out_rub). Без сумм из кэша реальных операций банка.
    """
    ensure_manual_operations_fresh()
    income = expense = 0.0
    inc_cnt = exp_cnt = 0

    for op_id, op in manual_operations.items():
        if op_id in hidden_operations:
            continue
        if restrict_month and not is_current_month(op.get("date", "")):
            continue
        amt = float(op.get("amount") or 0)
        if op.get("type") == "Credit":
            income += amt
            inc_cnt += 1
        elif op.get("type") == "Debit":
            expense += abs(amt)
            exp_cnt += 1

    extra_out = float((controller.config.get("transfers") or {}).get("total_out_rub", 0) or 0)

    if restrict_month:
        fake_deb_m = get_fake_expense_from_last_transfer()
        if fake_deb_m > 0:
            expense = round(expense + fake_deb_m, 2)
            exp_cnt += sum(1 for _ in _iter_fake_debit_ops_month())
        elif extra_out > 0:
            expense = round(expense + extra_out, 2)
        fake_cred_m = _fake_credit_month_total()
        if fake_cred_m > 0:
            income = round(income + fake_cred_m, 2)
            inc_cnt += sum(1 for _ in _iter_fake_credit_ops_month())
    else:
        d_sum = 0.0
        n_d = 0
        for _oid, amt, _op in _iter_fake_debit_ops_all():
            d_sum += amt
            n_d += 1
        expense = round(expense + d_sum, 2)
        exp_cnt += n_d
        if d_sum <= 0 and n_d <= 0 and extra_out > 0:
            expense = round(expense + extra_out, 2)
        c_sum = 0.0
        n_c = 0
        for _oid, amt, _op in _iter_fake_credit_ops_all():
            c_sum += amt
            n_c += 1
        income = round(income + c_sum, 2)
        inc_cnt += n_c

    return round(income, 2), round(expense, 2), inc_cnt, exp_cnt


def calculate_stats(restrict_month: bool = True):
    ensure_manual_operations_fresh()
    income = expense = 0.0
    inc_cnt = exp_cnt = 0

    def accumulate(op_id, op):
        nonlocal income, expense, inc_cnt, exp_cnt
        if op_id in hidden_operations:
            return
        if restrict_month and not is_current_month(op.get("date", "")):
            return
        amt = op.get("amount", 0)
        if op.get("type") == "Credit":
            income += amt
            inc_cnt += 1
        elif op.get("type") == "Debit":
            expense += abs(amt)
            exp_cnt += 1

    for op_id, op in operations_cache.items():
        accumulate(op_id, op)
    for op_id, op in manual_operations.items():
        accumulate(op_id, op)
    # Переводы из transfer.py: к сумме по кэшу добавляем только моки, которых ещё нет в кэше
    # (иначе двойной учёт, если тот же id уже в ленте). К гистограмме банка — полная сумма моков, см. get_panel_chart_display_totals.
    extra_out = float(controller.config.get("transfers", {}).get("total_out_rub", 0) or 0)
    if restrict_month:
        fake_total = get_fake_expense_from_last_transfer()
        fake_extra = get_fake_expense_not_in_operations_cache()
        if fake_total > 0:
            expense = round(expense + fake_extra, 2)
            exp_cnt += _count_fake_debit_ops_not_in_cache()
        else:
            expense = round(expense + extra_out, 2)
    else:
        fake_extra, fake_n = _fake_debit_extra_not_in_cache_all()
        expense = round(expense + fake_extra, 2)
        exp_cnt += fake_n
        if fake_extra <= 0 and fake_n <= 0:
            expense = round(expense + extra_out, 2)
    return round(income, 2), expense, inc_cnt, exp_cnt


def compute_manual_balance_adjustment() -> float:
    """Поправка баланса за текущий месяц: доходы ручных минус расходы (скрытые не считаем)."""
    ensure_manual_operations_fresh()
    adj = 0.0
    for oid, op in manual_operations.items():
        if oid in hidden_operations:
            continue
        if not is_current_month(op.get("date", "")):
            continue
        amt = float(op.get("amount") or 0)
        if op.get("type") == "Credit":
            adj += amt
        elif op.get("type") == "Debit":
            adj -= abs(amt)
    return round(adj, 2)


def build_operations_api_response():
    """Единый JSON для GET /api/operations (mitm и отдельный run.py / panel_server)."""
    ensure_manual_operations_fresh()
    global last_sync_time
    show_all = panel_include_all_cached_operations()
    month_ops = []
    for op_id, op in operations_cache.items():
        if not show_all and not is_current_month(op.get("date", "")):
            continue
        row = {
            "id": op_id,
            "date": op.get("date", ""),
            "sort_ts": date_str_to_millis(op.get("date", "")),
            "amount": op.get("amount", 0),
            "type": op.get("type", ""),
            "desc": op.get("description", ""),
            "bank": op.get("bank", ""),
            "manual": False,
        }
        fh = _fake_history_record_by_id(str(op_id))
        if fh and (show_all or _fake_op_in_current_month(fh)):
            row["fake_transfer"] = True
            rp = str(fh.get("receiver_phone") or fh.get("requisite_phone") or "").strip()
            rn = str(fh.get("receiver_name") or fh.get("requisite_sender_name") or "").strip()
            brx = str(fh.get("bank_receiver") or "").strip()
            row["title"] = str(fh.get("title") or row["desc"] or "").strip()
            row["subtitle"] = str(fh.get("subcategory") or "").strip()
            row["description"] = str(fh.get("description") or "").strip()
            row["sender_name"] = rn
            row["requisite_sender_name"] = rn
            row["phone"] = rp
            row["requisite_phone"] = rp
            row["card_number"] = str(fh.get("receiver_card") or fh.get("card_number") or "").strip()
            row["bank_preset"] = "sbp"
            fake_bank = _fake_bank_display_name(fh) or brx
            if fake_bank:
                row["bank"] = fake_bank
            line1 = get_op_description(fh) or fake_bank or row["desc"]
            if line1:
                row["desc"] = line1
        month_ops.append(row)
    manual_entries = [
        (oid, o)
        for oid, o in manual_operations.items()
        if show_all or is_current_month(o.get("date", ""))
    ]
    for op_id, op in manual_entries:
        line1 = (op.get("title") or op.get("requisite_phone") or op.get("phone") or op.get("description") or "")
        ts = operation_time_ms(op)
        if ts <= 0:
            ts = date_str_to_millis(op.get("date", ""))
        month_ops.append({
            "id": op_id,
            "date": op.get("date", ""),
            "sort_ts": ts,
            "amount": op.get("amount", 0),
            "type": op.get("type", ""),
            "desc": line1,
            "title": op.get("title") or "",
            "subtitle": op.get("subtitle") or "",
            "description": op.get("description") or "",
            "sender_name": op.get("sender_name") or "",
            "requisite_sender_name": op.get("requisite_sender_name") or op.get("sender_name") or "",
            "bank": op.get("bank", ""),
            "bank_preset": op.get("bank_preset") or "",
            "phone": op.get("phone") or "",
            "requisite_phone": op.get("requisite_phone") or op.get("phone") or "",
            "receipt_phone": op.get("receipt_phone") or "",
            "card_number": op.get("card_number") or "",
            "manual": True,
        })
    skip_fake = set(operations_cache.keys()) | set(manual_operations.keys())
    month_ops.extend(_fake_transfer_ops_for_panel(skip_fake, month_only=not show_all))
    month_ops.sort(key=lambda x: (x.get("sort_ts", 0), 1 if x.get("manual") else 0), reverse=True)
    display_income, display_expense, real_inc_cnt, real_exp_cnt = get_panel_chart_display_totals()
    return {
        "operations": month_ops,
        "hidden": list(hidden_operations),
        "stats": {
            "income": display_income,
            "expense": display_expense,
            "income_count": real_inc_cnt,
            "expense_count": real_exp_cnt,
        },
        "last_sync": last_sync_time.strftime("%d.%m.%Y %H:%M:%S") if last_sync_time else None,
    }


def response(flow: http.HTTPFlow) -> None:
    global operations_cache, hidden_operations, last_sync_time
    url = flow.request.pretty_url
    ensure_manual_operations_fresh()

    if not is_bank_flow(flow):
        return
    if not flow.response:
        return
    ensure_response_decoded(flow)
    if not flow.response.text:
        if bank_debug_enabled():
            print(f"[history] пустой ответ: {url[:120]}")
        return
    if not is_jsonish_response(flow):
        return

    if url_prohibit_proxy_json_mutation(url):
        return

    if bank_debug_enabled():
        print(f"[history] Вижу запрос: {flow.request.method} {url}")

    try:
        data = json.loads(flow.response.text)
        try:
            req_text = flow.request.get_text(strict=False)
        except Exception:
            req_text = ""
        ua_hdr = flow.request.headers.get("User-Agent", "")
        if isinstance(ua_hdr, bytes):
            ua = ua_hdr.decode("utf-8", "replace")
        else:
            ua = ua_hdr or ""
        referer = flow.request.headers.get("Referer", "")
        if isinstance(referer, bytes):
            referer = referer.decode("utf-8", "replace")
        ctx_stmt = _flow_is_statements_certificates_context(url, referer, req_text)

        if not ctx_stmt:
            ops = extract_operations(data)
            if ops:
                new_ops = 0
                for op in ops:
                    op_id = op.get('id')
                    if not op_id or op_id in operations_cache or op_id in manual_operations:
                        continue
                    operations_cache[op_id] = {
                        "id": op_id,
                        "date": get_op_date(op),
                        "amount": get_op_amount(op),
                        "type": get_op_type(op),
                        "description": get_op_description(op),
                        "bank": get_op_bank(op)
                    }
                    new_ops += 1
                if new_ops:
                    print(f"[history] Добавлено {new_ops} новых операций")
                    clean_old_ops()
                    last_sync_time = datetime.now()

        filtered = apply_hidden_operations_filter(data, url, referer, req_text)
        injected = False if ctx_stmt else inject_manual_into_response(data, url, req_text, ua, referer)

        if injected or filtered:
            flow.response.text = json.dumps(data, ensure_ascii=False)
    except Exception as e:
        print(f"[history] Ошибка при разборе/модификации ответа: {e}")

def request(flow: http.HTTPFlow) -> None:
    global hidden_operations, last_sync_time
    ensure_manual_operations_fresh()

    if flow.request.port != 8082:
        return

    path = flow.request.path
    path_only = path.split("?", 1)[0]

    cors_json = {"Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*"}

    if flow.request.method == "OPTIONS" and path_only in ("/api/panel_income_expense", "/api/hide_all_operations", "/api/show_all_operations"):
        flow.response = http.Response.make(
            204,
            b"",
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
        return

    if path_only == "/api/panel_income_expense" and flow.request.method == "GET":
        di, de, _, _ = get_panel_chart_display_totals()
        flow.response = http.Response.make(
            200,
            json.dumps({"income": di, "expense": de}, ensure_ascii=False).encode("utf-8"),
            cors_json,
        )
        return

    if flow.request.method == "POST" and path_only == "/api/hide_all_operations":
        ensure_manual_operations_fresh()
        hidden_operations.update(operations_cache.keys())
        hidden_operations.update(manual_operations.keys())
        for p in _last_transfer_json_paths():
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    td = json.load(f)
                for fo in td.get("fake_history") or []:
                    if isinstance(fo, dict) and fo.get("id"):
                        hidden_operations.add(str(fo.get("id")))
            except Exception:
                pass
        flow.response = http.Response.make(
            200,
            json.dumps({"status": "ok", "count": len(hidden_operations)}, ensure_ascii=False).encode("utf-8"),
            cors_json,
        )
        print(f"[history] Скрыты все известные операции: {len(hidden_operations)} id")
        return

    if flow.request.method == "POST" and path_only == "/api/show_all_operations":
        hidden_operations.clear()
        flow.response = http.Response.make(
            200,
            json.dumps({"status": "ok"}, ensure_ascii=False).encode("utf-8"),
            cors_json,
        )
        print("[history] Показаны все операции (сброс скрытых)")
        return

    if path == "/api/operations":
        response_data = build_operations_api_response()
        flow.response = http.Response.make(
            200,
            json.dumps(response_data, ensure_ascii=False).encode('utf-8'),
            {"Content-Type": "application/json"}
        )
        if bank_debug_enabled():
            print(f"[history] Панель: {len(response_data.get('operations') or [])} операций")
        return

    if flow.request.method == "POST" and path == "/api/operations/add":
        try:
            body = json.loads(flow.request.text or "{}")
            direction = body.get("direction", "out")
            amount = abs(float(body.get("amount") or 0))
            bank = (body.get("bank") or "").strip()
            desc = (body.get("description") or "").strip()
            title = (body.get("title") or body.get("phone") or "").strip()
            subtitle = (body.get("subtitle") or "").strip()
            sender_name = (body.get("sender_name") or "").strip()
            requisite_sender_name = (body.get("requisite_sender_name") or sender_name).strip()
            requisite_phone = (body.get("requisite_phone") or body.get("phone") or "").strip()
            bank_preset = (body.get("bank_preset") or "custom").strip().lower() or "custom"
            op_type = "Debit" if direction == "out" else "Credit"
            date_str, op_ms = parse_panel_datetime_iso(body.get("datetime"))
            op_id = "m_" + uuid.uuid4().hex[:12]
            manual_operations[op_id] = {
                "id": op_id,
                "date": date_str,
                "amount": amount,
                "type": op_type,
                "description": desc,
                "bank": bank,
                "title": title,
                "subtitle": subtitle,
                "sender_name": sender_name,
                "requisite_sender_name": requisite_sender_name,
                "phone": (body.get("phone") or "").strip(),
                "requisite_phone": requisite_phone,
                "receipt_phone": (body.get("receipt_phone") or "").strip(),
                "card_number": (body.get("card_number") or "").strip(),
                "bank_preset": bank_preset,
                "operationTime": {"milliseconds": op_ms, "seconds": op_ms / 1000.0},
            }
            save_manual_operations()
            
            # Auto-generate PDF receipt
            op_data = {
                "id": op_id,
                "date": date_str,
                "amount": amount,
                "type": op_type,
                "bank": bank or bank_preset.title(),
                "title": title,
                "phone": requisite_phone or body.get("phone", ""),
                "receipt_phone": manual_operations[op_id].get("receipt_phone") or "",
                "sender_name": sender_name,
                "requisite_sender_name": requisite_sender_name,
            }
            receipt_path = func.generate_operation_receipt(op_data)
            if receipt_path:
                manual_operations[op_id]["pdf_path"] = receipt_path
                save_manual_operations()

            sync_panel_income_expense_with_operations()
            flow.response = http.Response.make(
                200,
                json.dumps({"status": "ok", "id": op_id, "receipt_path": receipt_path}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"}
            )
            print(f"[history] Добавлена ручная операция {op_id} ({op_type}, {amount}), receipt: {receipt_path}")
        except Exception as e:
            flow.response = http.Response.make(
                400,
                json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"}
            )
        return

    if flow.request.method == "POST" and path == "/api/operations/delete":
        try:
            body = json.loads(flow.request.text or "{}")
            op_id = body.get("id") or ""
            if op_id in manual_operations:
                del manual_operations[op_id]
                hidden_operations.discard(op_id)
                save_manual_operations()
                sync_panel_income_expense_with_operations()
                flow.response = http.Response.make(200, json.dumps({"status": "ok"}).encode("utf-8"), {"Content-Type": "application/json"})
                print(f"[history] Удалена ручная операция {op_id}")
            elif remove_fake_transfer_operation(op_id):
                sync_panel_income_expense_with_operations()
                flow.response = http.Response.make(200, json.dumps({"status": "ok"}).encode("utf-8"), {"Content-Type": "application/json"})
                print(f"[history] Удалена операция мок‑перевода {op_id}")
            else:
                flow.response = http.Response.make(404, json.dumps({"error": "not found"}).encode("utf-8"), {"Content-Type": "application/json"})
        except Exception as e:
            flow.response = http.Response.make(400, json.dumps({"error": str(e)}).encode("utf-8"), {"Content-Type": "application/json"})
        return

    if flow.request.method == "POST" and path == "/api/operations/update":
        try:
            body = json.loads(flow.request.text or "{}")
            op_id = (body.get("id") or "").strip()
            if not op_id or op_id not in manual_operations:
                flow.response = http.Response.make(
                    404,
                    json.dumps({"error": "not found"}, ensure_ascii=False).encode("utf-8"),
                    {"Content-Type": "application/json"},
                )
                return
            rec = manual_operations[op_id]
            if "amount" in body:
                rec["amount"] = abs(float(body.get("amount") or 0))
            if "type" in body and body.get("type") in ("Credit", "Debit"):
                rec["type"] = body["type"]
            if "direction" in body:
                rec["type"] = "Debit" if body.get("direction") == "out" else "Credit"
            for k in ("title", "subtitle", "description", "bank", "bank_preset", "phone", "card_number", "sender_name", "requisite_phone", "requisite_sender_name", "receipt_phone"):
                if k in body:
                    rec[k] = (body.get(k) or "").strip() if isinstance(body.get(k), str) else body.get(k)
            if body.get("datetime"):
                dstr, op_ms = parse_panel_datetime_iso(body["datetime"])
                rec["date"] = dstr
                rec["operationTime"] = {"milliseconds": op_ms, "seconds": op_ms / 1000.0}
            save_manual_operations()
            sync_panel_income_expense_with_operations()
            flow.response = http.Response.make(
                200,
                json.dumps({"status": "ok", "id": op_id}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
            print(f"[history] Обновлена ручная операция {op_id}")
        except Exception as e:
            flow.response = http.Response.make(
                400,
                json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json"},
            )
        return

    if flow.request.method == "POST" and path == "/api/toggle":
        body = flow.request.text
        op_id = parse_qs(body).get("id", [""])[0]
        if op_id and (op_id in operations_cache or op_id in manual_operations or op_id_in_fake_history_files(op_id)):
            if op_id in hidden_operations:
                hidden_operations.remove(op_id)
                print(f"[history] Операция показана: {op_id[:8]}")
            else:
                hidden_operations.add(op_id)
                print(f"[history] Операция скрыта: {op_id[:8]}")
            flow.response = http.Response.make(200, b"OK", {"Content-Type": "text/plain"})
            return

print("[+] history.py загружен")