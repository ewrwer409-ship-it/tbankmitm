# Трафик Т‑Банка / приложения (часть запросов — по Host/SNI, без «tbank» в полном URL).
import os

_BANK_KEYS = (
    "tbank",
    "tinkoff",
    "t-co.ru",
    "tinkoffbank",
)


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


def is_bank_flow(flow) -> bool:
    """Считать запрос банковским, если tbank/tinkoff и т.д. есть в URL, Host или SNI."""
    blob = _flow_identity_blob(flow)
    ok = any(k in blob for k in _BANK_KEYS)
    if bank_debug_enabled() and ok and not is_bank_url(flow.request.pretty_url or ""):
        print(f"[bank_filter] трафик по Host/SNI (не только URL): {blob[:200]}")
    return ok


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
