"""
WebSocket: тот же JSON-пайплайн, что и HTTP (см. bank_json_pipeline.py), иначе push
обходит подмену с REST.

Отключить: TBANKMITM_WS_BALANCE_PATCH=0
Путь upgrade: TBANKMITM_WS_BALANCE_PATH_SUBSTR=/push
"""
from __future__ import annotations

import json
import os
import sys

from mitmproxy import http

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _script_dir)

from bank_filter import bank_debug_enabled, is_bank_flow
from balance import build_balance_test_data, _patch_first_balance_like_node
from bank_json_pipeline import try_apply_balance_tree

_WS_PATCH = os.environ.get("TBANKMITM_WS_BALANCE_PATCH", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_PATH_HINT = os.environ.get("TBANKMITM_WS_BALANCE_PATH_SUBSTR", "/push").strip().lower() or "/push"

print(
    "[+] ws_bank_push_patch загружен "
    "(патч баланса в JSON по WebSocket; TBANKMITM_WS_BALANCE_PATCH=0 отключает)"
)


def websocket_message(flow: http.HTTPFlow) -> None:
    if not _WS_PATCH:
        return
    try:
        ws = flow.websocket
        if ws is None:
            return
        msg = ws.messages[-1]
    except (AttributeError, IndexError):
        return
    if msg.from_client:
        return
    if not is_bank_flow(flow):
        return
    path = (flow.request.path or "").lower()
    if _PATH_HINT not in path:
        return

    raw: bytes = msg.content
    if not raw or len(raw) > 4 * 1024 * 1024:
        return
    if raw.lstrip()[:1] not in (b"{", b"["):
        return

    td = build_balance_test_data()
    if not td:
        return

    url = flow.request.pretty_url or ""
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(data, (dict, list)):
        return

    if not try_apply_balance_tree(
        url=url,
        source="websocket",
        data=data,
        test_data=td,
        patch_fn=_patch_first_balance_like_node,
        body_text=text,
    ):
        return
    try:
        msg.content = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if bank_debug_enabled():
            print(f"[ws_bank_push_patch] JSON pipeline {flow.request.host}{path[:80]}")
    except Exception:
        pass
