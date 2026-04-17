"""
Append-only log of destinations seen by mitmproxy (CONNECT, TLS SNI, HTTP).

For MDM/VPN allowlists or debugging: shows where the client (e.g. iPhone) connects.

Environment:
  TBANKMITM_TRAFFIC_LOG_PATH  Log file path (default: traffic_hosts.log next to this script).
  TBANKMITM_TRAFFIC_LOG=0     Disable file logging (stdout still works if TBANKMITM_TRAFFIC_STDOUT=1).
  TBANKMITM_TRAFFIC_STDOUT=1  Also print each line to the console.

Log format: tab-separated UTC time, kind, client_ip, host, port, detail
  kind: connect | tls_sni | http
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from mitmproxy import http
from mitmproxy.tls import ClientHelloData

_script_dir = os.path.dirname(os.path.abspath(__file__))

_enabled = os.environ.get("TBANKMITM_TRAFFIC_LOG", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
_stdout = os.environ.get("TBANKMITM_TRAFFIC_STDOUT", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_log_path = (
    os.environ.get("TBANKMITM_TRAFFIC_LOG_PATH", "").strip()
    or os.path.join(_script_dir, "traffic_hosts.log")
)


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _client_ip(flow: http.HTTPFlow) -> str:
    try:
        p = flow.client_conn.peername
        if p and len(p) >= 1:
            return str(p[0])
    except Exception:
        pass
    return ""


def _write_line(parts: list[str]) -> None:
    line = "\t".join(parts) + "\n"
    if _stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    if not _enabled:
        return
    try:
        with open(_log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
    except OSError:
        pass


class TrafficLogHosts:
    def http_connect(self, flow: http.HTTPFlow) -> None:
        host = flow.request.host or ""
        port = flow.request.port or 0
        _write_line(
            [
                _ts(),
                "connect",
                _client_ip(flow),
                host,
                str(port),
                "",
            ]
        )

    def request(self, flow: http.HTTPFlow) -> None:
        if flow.request.method.upper() == "CONNECT":
            return
        host = flow.request.host or flow.request.pretty_host or ""
        port = flow.request.port or 0
        detail = f"{flow.request.method} {flow.request.path}"
        _write_line(
            [
                _ts(),
                "http",
                _client_ip(flow),
                host,
                str(port),
                detail,
            ]
        )

    def tls_clienthello(self, data: ClientHelloData) -> None:
        try:
            sni = (data.client_hello.sni or "").strip()
        except Exception:
            sni = ""
        if not sni:
            return
        client_ip = ""
        try:
            ctx = data.context
            conn = getattr(ctx, "client", None)
            if conn is not None and conn.peername and len(conn.peername) >= 1:
                client_ip = str(conn.peername[0])
        except Exception:
            pass
        _write_line([_ts(), "tls_sni", client_ip, sni, "443", ""])


addons = [TrafficLogHosts()]
