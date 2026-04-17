"""
Фиксирует HTTP-ответ сервера ДО правок остальных аддонов.

В mitmproxy хуки response вызываются в обратном порядке к порядку аргументов -s,
поэтому этот скрипт должен быть ПОСЛЕДНИМ в mitm_addon_chain.py — тогда его
response() выполняется первым и видит исходный status/body.

Переменные окружения:
  TBANKMITM_UPSTREAM_LOG=1           включить (по умолчанию выключено)
  TBANKMITM_UPSTREAM_LOG_PATH=...    файл строк (табы); по умолчанию upstream_http.log рядом со скриптом
  TBANKMITM_UPSTREAM_ERRORS_ONLY=1 только строки с HTTP >= 400
  TBANKMITM_UPSTREAM_DUMP_SUBSTR=   подстроки URL через запятую — для них сохранять полное тело ответа
  TBANKMITM_UPSTREAM_DUMP_DIR=...   каталог для дампов тел (по умолчанию upstream_dumps рядом со скриптом)
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from datetime import datetime, timezone

from mitmproxy import http

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)
from bank_filter import ensure_response_decoded, is_bank_flow  # noqa: E402


def _env_on(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_slug(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[^\w\-.]+", "_", s, flags=re.UNICODE)
    return s[:max_len] if len(s) > max_len else s


class UpstreamResponseLog:
    def response(self, flow: http.HTTPFlow) -> None:
        if not _env_on("TBANKMITM_UPSTREAM_LOG"):
            return
        if not flow.response:
            return
        if flow.request.method.upper() == "CONNECT":
            return
        if not is_bank_flow(flow):
            return

        url = flow.request.pretty_url or ""
        sc = int(flow.response.status_code or 0)
        errors_only = _env_on("TBANKMITM_UPSTREAM_ERRORS_ONLY")

        ensure_response_decoded(flow)
        body = flow.response.text or ""
        blen = len(body.encode("utf-8", errors="replace"))

        substr_env = os.environ.get("TBANKMITM_UPSTREAM_DUMP_SUBSTR", "").strip()
        dump_substrs = [x.strip().lower() for x in substr_env.split(",") if x.strip()]
        url_l = url.lower()
        dump_match = bool(dump_substrs) and any(sub in url_l for sub in dump_substrs)

        if errors_only and sc < 400 and not dump_match:
            return

        log_path = os.environ.get("TBANKMITM_UPSTREAM_LOG_PATH", "").strip() or os.path.join(
            _script_dir, "upstream_http.log"
        )

        line = "\t".join(
            [
                _ts(),
                flow.request.method or "",
                url,
                str(sc),
                str(blen),
            ]
        )

        try:
            with open(log_path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if sc >= 400:
            try:
                sys.stderr.write(f"[upstream_log] {flow.request.method} {sc} {url[:160]}\n")
            except Exception:
                pass

        if dump_match and body:
            dump_dir = os.environ.get("TBANKMITM_UPSTREAM_DUMP_DIR", "").strip() or os.path.join(
                _script_dir, "upstream_dumps"
            )
            try:
                os.makedirs(dump_dir, exist_ok=True)
                h = hashlib.sha256(url.encode("utf-8", errors="replace")).hexdigest()[:12]
                fname = f"{_ts().replace(':', '-')}_{sc}_{_safe_slug(flow.request.path or 'path')}_{h}.txt"
                fpath = os.path.join(dump_dir, fname)
                with open(fpath, "w", encoding="utf-8", newline="\n") as df:
                    df.write(body)
                try:
                    sys.stderr.write(f"[upstream_log] dump body → {fpath}\n")
                except Exception:
                    pass
            except OSError:
                pass


addons = [UpstreamResponseLog()]
