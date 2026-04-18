"""
Единый список mitmproxy-аддонов для Windows (_proxy_cmd.bat) и Linux (start_vps.sh).
Менять порядок / состав только здесь.
"""
from __future__ import annotations

import os

# Порядок важен: tls passthrough первым; operation_detail после history; panel после name/reki; инжектор в конце.
MITM_ADDON_SCRIPTS: tuple[str, ...] = (
    "tls_passthrough_hosts.py",
    "transfer.py",
    "controller.py",
    "balance.py",
    "history.py",
    "operation_detail.py",
    "name.py",
    "reki.py",
    "panel_bridge.py",
    "browser_ops_injector.py",
    "tbank_sbp_debit_injector.py",
)


def build_mitmdump_argv(script_dir: str) -> list[str]:
    """Аргументы для mitmdump() (как sys.argv[1:])."""
    argv: list[str] = []
    for name in MITM_ADDON_SCRIPTS:
        path = os.path.join(script_dir, name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Mitm addon script not found: {path}")
        argv.extend(["-s", path])

    listen = os.environ.get("TBANKMITM_PROXY_LISTEN_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = os.environ.get("TBANKMITM_PROXY_PORT", "8082").strip() or "8082"

    argv.extend(
        [
            "--listen-host",
            listen,
            "-p",
            port,
            "--set",
            "block_global=false",
            "--set",
            "ssl_insecure=true",
            "--set",
            "http2=false",
        ]
    )
    return argv
