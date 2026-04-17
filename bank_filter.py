# Трафик Т‑Банка / приложения (часть запросов — по Host/SNI, без «tbank» в полном URL).
from __future__ import annotations

import json
import os
from typing import Any

_BANK_KEYS = (
    "tbank",
    "tinkoff",
    "t-co.ru",
    "tinkoffbank",
    # Встраиваемый банк / iOS (Drive Transit и др.): api.*.t-bank-app.ru — без суффикса «tbank» подряд.
    "t-bank-app",
)


def is_embedded_tbank_app_hostname(hostname: str) -> bool:
    """Встраиваемый клиент Т‑Банка (host вида *.t-bank-app.ru / *.t-bank-app.su)."""
    h = (hostname or "").lower()
    return bool(h) and "t-bank-app" in h


def is_main_tbank_web_hostname(hostname: str) -> bool:
    """
    Сайт Т‑Банка в браузере (*.tbank.ru, *tinkoff*), не приложение t-bank-app.
    Используется, чтобы отличать «классический веб» от встраиваемого API при одном и том же Safari в UA.
    """
    if is_embedded_tbank_app_hostname(hostname):
        return False
    h = (hostname or "").lower().strip(".")
    if not h:
        return False
    return h.endswith("tbank.ru") or h.endswith("tinkoff.ru") or h in ("tbank.ru", "www.tbank.ru")


def is_bank_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return any(k in u for k in _BANK_KEYS)


def _flow_identity_blob(flow) -> str:
    """URL + имя хоста из запроса, заголовок Host, SNI TLS (важно для Т‑Банка)."""
    parts = []
    try:
        r = flow.request
        parts.append(r.pretty_url or "")
        parts.append(getattr(r, "host", "") or "")
        h = r.headers.get("Host") or r.headers.get("host")
        if h:
            parts.append(h)
    except Exception:
        pass
    try:
        cn = getattr(flow, "client_conn", None)
        if cn is not None:
            sni = getattr(cn, "sni", None)
            if sni:
                parts.append(str(sni))
    except Exception:
        pass
    return " ".join(str(p) for p in parts).lower()


_ios_gate_cache: dict[str, Any] | None = None
_ios_gate_cache_mtime: float | None = None


def _config_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def _load_config_raw() -> dict[str, Any]:
    path = _config_path()
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _ua_looks_like_desktop_or_mobile_safari(user_agent: str) -> bool:
    """Типичный браузерный UA (десктоп или мобильный Safari / Chrome WebView с Safari)."""
    ua = (user_agent or "").lower()
    if not ua:
        return False
    if any(x in ua for x in ("mozilla/", "chrome/", "edg/", "firefox/")):
        return True
    if "applewebkit" in ua and "mobile/" in ua and "safari/" in ua:
        return True
    return "safari/" in ua


def _looks_like_ios_native_urlsession(user_agent: str) -> bool:
    """
    Запросы многих нативных iOS-приложений (в т.ч. оболочек банка): CFNetwork + Darwin,
    без полноценного браузерного AppleWebKit/Safari как у Safari.
    """
    ua = user_agent or ""
    if "CFNetwork" not in ua or "Darwin" not in ua:
        return False
    if _ua_looks_like_desktop_or_mobile_safari(ua):
        return False
    return True


def _normalize_markers(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for x in raw:
        s = str(x).strip()
        if s:
            out.append(s)
    return out


def _default_markers_for_bundle(bundle_id: str) -> list[str]:
    b = (bundle_id or "").strip()
    if not b:
        return []
    extra: list[str] = []
    if "delivery.drive" in b or "drive.almagul" in b:
        extra.extend(
            (
                "com.delivery.drive",
                "delivery.drive",
                "almagul.daurbayeva",
            )
        )
    parts = b.split(".")
    if len(parts) >= 2:
        extra.append(f"{parts[-2]}.{parts[-1]}")
    seen: set[str] = set()
    out: list[str] = []
    for x in extra:
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _load_ios_gate_config() -> dict[str, Any]:
    """
    ios_app.gate_mode:
      relaxed (default) — bundle/markers в заголовках ИЛИ нативный iOS URLSession UA;
      strict — только явные bundle/markers в заголовках;
      off — как без клиентского фильтра (только tbank/tinkoff по хосту).

    Env: TBANKMITM_CLIENT_GATE_OFF=1, TBANKMITM_IOS_GATE_MODE=relaxed|strict|off,
         TBANKMITM_ONLY_USER_AGENT_CONTAINS — одна подстрока (strict-логика: любой заголовок).
    """
    global _ios_gate_cache, _ios_gate_cache_mtime
    if os.environ.get("TBANKMITM_CLIENT_GATE_OFF", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return {"active": False}

    env_one = os.environ.get("TBANKMITM_ONLY_USER_AGENT_CONTAINS", "").strip()
    env_mode = os.environ.get("TBANKMITM_IOS_GATE_MODE", "").strip().lower()

    path = _config_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if _ios_gate_cache is not None and mtime == _ios_gate_cache_mtime and not env_one and not env_mode:
        return _ios_gate_cache

    cfg = _load_config_raw()
    sub = cfg.get("ios_app") if isinstance(cfg.get("ios_app"), dict) else {}
    bundle = (
        str(sub.get("bundle_id") or cfg.get("ios_app_bundle_id") or "").strip()
    )
    markers = _normalize_markers(sub.get("user_agent_markers"))
    legacy = (cfg.get("only_user_agent_contains") or "").strip()
    if legacy and legacy not in markers:
        markers.insert(0, legacy)

    mode = str(sub.get("gate_mode") or "relaxed").strip().lower()
    if mode not in ("relaxed", "strict", "off"):
        mode = "relaxed"

    if env_mode in ("relaxed", "strict", "off"):
        mode = env_mode

    if env_one:
        return {
            "active": True,
            "mode": "strict",
            "bundle_id": "",
            "markers": [env_one],
            "use_ios_ua_heuristic": False,
        }

    if mode == "off":
        out = {"active": False}
    elif bundle or markers:
        merged_markers = list(markers)
        for m in _default_markers_for_bundle(bundle):
            if m.lower() not in {x.lower() for x in merged_markers}:
                merged_markers.append(m)
        out = {
            "active": True,
            "mode": mode,
            "bundle_id": bundle,
            "markers": merged_markers,
            "use_ios_ua_heuristic": mode == "relaxed",
        }
    else:
        out = {"active": False}

    _ios_gate_cache = out
    _ios_gate_cache_mtime = mtime
    return out


def _all_header_values_blob(flow) -> str:
    parts: list[str] = []
    try:
        for _k, v in flow.request.headers.items():
            parts.append(str(v or ""))
    except Exception:
        pass
    return " ".join(parts)


def _client_gate_passes(flow, g: dict[str, Any]) -> bool:
    if not g.get("active"):
        return True
    blob = _all_header_values_blob(flow).lower()
    bundle = (g.get("bundle_id") or "").strip().lower()
    if bundle and bundle in blob:
        return True
    for m in g.get("markers") or []:
        if str(m).lower() in blob:
            return True
    if g.get("use_ios_ua_heuristic"):
        try:
            ua = flow.request.headers.get("User-Agent", "") or ""
        except Exception:
            ua = ""
        if _looks_like_ios_native_urlsession(ua):
            return True
    return False


def is_bank_flow(flow) -> bool:
    """Считать запрос банковским, если tbank/tinkoff и т.д. есть в URL, Host или SNI."""
    blob = _flow_identity_blob(flow)
    ok = any(k in blob for k in _BANK_KEYS)
    if not ok:
        return False
    # Встроенный клиент (*.t-bank-app.ru / tapi…): часть запросов идёт без явных ios_app
    # маркеров в заголовках — клиентский gate ломался и отсекал ВЕСЬ обходящий JSON.
    try:
        rh = (getattr(flow.request, "host", None) or "").lower()
    except Exception:
        rh = ""
    if "t-bank-app" in rh:
        return True
    gate_cfg = _load_ios_gate_config()
    if gate_cfg.get("active") and not _client_gate_passes(flow, gate_cfg):
        return False
    if bank_debug_enabled() and ok and not is_bank_url(flow.request.pretty_url or ""):
        print(f"[bank_filter] трафик по Host/SNI (не только URL): {blob[:200]}")
    return True


def ensure_response_decoded(flow) -> None:
    try:
        resp = flow.response
        if not resp:
            return
        enc = (resp.headers.get("Content-Encoding") or "").strip().lower()
        if enc and enc != "identity":
            resp.decode()
    except Exception:
        pass


def is_jsonish_response(flow) -> bool:
    """Content-Type с json или тело похоже на JSON (часть ответов Т‑Банка без application/json)."""
    ct = (flow.response.headers.get("content-type") or "").lower()
    if "json" in ct or "graphql" in ct:
        return True
    txt = (flow.response.text or "").lstrip()
    if not txt:
        return False
    return txt[0] in "{["


def bank_debug_enabled() -> bool:
    return os.environ.get("BANK_DEBUG", "").strip() in ("1", "true", "yes", "on")


def text_indicates_statements_spravki(
    url: str = "", referer: str = "", request_body: str = ""
) -> bool:
    """
    Экран «Справки» (/mybank/statements): путь в URL, Referer или строка в теле (GraphQL),
    т.к. для api.* Referer часто обрезан до origin без /mybank/statements.
    """
    blob = f"{url or ''} {referer or ''} {request_body or ''}".lower()
    if "/mybank/statements" in blob:
        return True
    if "mybank%2fstatements" in blob:
        return True
    if "mybank%252fstatements" in blob:
        return True
    return False


def url_prohibit_proxy_json_mutation(url: str) -> bool:
    """
    Ответы, которые нельзя json.loads + патчить прокси-скриптами: ломается Tramvai / «Справки».

    - /api/cfg/web-gateway/getResponse — огромное дерево микрофронтов; apply_hidden / inject
      находили «кандидатов»-списки и портили JSON при has_operation_candidates.
    - cx-evolution-api … documents — запросы справок (filter-new-document и т.д.).
    """
    u = (url or "").lower()
    if "/api/cfg/web-gateway" in u:
        return True
    if "cx-evolution-api" in u and "document" in u:
        return True
    return False


def flow_statements_spravki_context(flow) -> bool:
    """То же, что text_indicates_statements_spravki, но из HTTPFlow (URL + Referer + тело запроса)."""
    try:
        req = flow.request
        url = req.pretty_url or ""
        ref = req.headers.get("Referer") or ""
        if isinstance(ref, bytes):
            ref = ref.decode("utf-8", "replace")
        body = ""
        try:
            body = req.get_text(strict=False) or ""
        except Exception:
            pass
        return text_indicates_statements_spravki(url, ref, body)
    except Exception:
        return False
