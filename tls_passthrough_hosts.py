"""
Проброс TLS без расшифровки mitm для хостов, где посредник даёт 403 (S3 / WAF по JA3).

Через класс + addons — надёжнее, чем только функция tls_clienthello в скрипте (mitmproxy).

Отключить: TBANKMITM_TLS_PASSTHROUGH=0
Доп. regex по SNI: TBANKMITM_TLS_PASSTHROUGH_HOSTS
Лог при срабатывании: TBANKMITM_TLS_DEBUG=1
"""
from __future__ import annotations

import os
import re

_TLS_PT = os.environ.get("TBANKMITM_TLS_PASSTHROUGH", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_TLS_DEBUG = os.environ.get("TBANKMITM_TLS_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

_EXTRA_RAW = os.environ.get("TBANKMITM_TLS_PASSTHROUGH_HOSTS", "").strip()
try:
    _EXTRA_RE = re.compile(_EXTRA_RAW, re.I) if _EXTRA_RAW else None
except re.error:
    _EXTRA_RE = None

# Хосты, для которых типичен 403 при перехваченном TLS (микрофронты «Справок» и др.).
_DEFAULT_SNIS = (
    "s3-msk.tinkoff.ru",
    "s3-msk.tbank.ru",
    "s3.tinkoff.ru",
    "s3.tbank.ru",
)


def _sni_matches(sni_l: str) -> bool:
    for h in _DEFAULT_SNIS:
        if sni_l == h or sni_l.endswith("." + h):
            return True
    if _EXTRA_RE is not None and _EXTRA_RE.search(sni_l):
        return True
    return False


class TlsPassthroughHosts:
    def tls_clienthello(self, data) -> None:
        if not _TLS_PT:
            return
        try:
            sni = (data.client_hello.sni or "").strip()
        except Exception:
            return
        if not sni:
            return
        sni_l = sni.lower()
        if not _sni_matches(sni_l):
            return
        data.ignore_connection = True
        if _TLS_DEBUG:
            print(f"[tls_passthrough] ignore_connection SNI={sni!r}")


addons = [TlsPassthroughHosts()]
