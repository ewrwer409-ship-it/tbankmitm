from mitmproxy import http
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_filter import (
    is_bank_flow,
    ensure_response_decoded,
    bank_debug_enabled,
    is_jsonish_response,
    flow_statements_spravki_context,
    url_prohibit_proxy_json_mutation,
)
from bank_json_pipeline import is_force_balance_url, try_apply_balance_tree

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")


def _effective_balance(base: float) -> float:
    try:
        import history as history_mod

        adj = history_mod.compute_manual_balance_adjustment()
        return round(float(base) + float(adj), 2)
    except Exception:
        return float(base)

def get_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["balance"]


def build_balance_test_data() -> dict | None:
    """Те же числа и карта, что подставляются в HTTP (для патча JSON в WebSocket push)."""
    try:
        balance_cfg = get_config()
        return {
            "new_balance": _effective_balance(balance_cfg["new_balance"]),
            "new_collect_sum": balance_cfg["new_collect_sum"],
            "new_card_number": balance_cfg["new_card_number"],
        }
    except Exception:
        return None


def _strip_card_previews(node: dict) -> None:
    """На главной mybank оставляем только одну основную карту-превью без дублей."""
    if not isinstance(node, dict):
        return
    for key in ("cards", "cardList", "previewCards"):
        if key in node and isinstance(node.get(key), list):
            items = [x for x in node[key] if isinstance(x, dict)]
            node[key] = items[:1]
    for key in ("cardsCount", "cardCount"):
        if key in node:
            count = 0
            for list_key in ("cards", "cardList", "previewCards"):
                if isinstance(node.get(list_key), list) and node[list_key]:
                    count = len(node[list_key])
                    break
            node[key] = count


def _patch_money_value_dict(block: dict, balance_value: float) -> bool:
    if isinstance(block, dict) and "value" in block:
        try:
            float(block["value"])  # только числовые «денежные» узлы
            block["value"] = balance_value
            return True
        except (TypeError, ValueError):
            return False
    return False


def _patch_first_balance_like_node(node, balance_value: float, collect_sum, card_number: str) -> bool:
    """Фолбэк для новых ответов mybank / встроенного клиента (uiobject, shared resources и т.д.)."""
    _money_keys = (
        "availableBalance",
        "moneyAmount",
        "balance",
        "accountBalance",
        "totalBalance",
        "factBalance",
        "currentBalance",
        "remainder",
        # Долги / кредитный блок (часто на account_details / full_debt в приложении).
        "ownFunds",
        "debtAmount",
        "fullDebtAmount",
        "principalDebt",
        "creditBalance",
        "available_balance",
        "total_balance",
        "walletBalance",
        "mainBalance",
    )

    if isinstance(node, dict):
        has_balance_field = False

        for key in _money_keys:
            block = node.get(key)
            if isinstance(block, dict) and _patch_money_value_dict(block, balance_value):
                has_balance_field = True

        if has_balance_field:
            if "collectSum" in node:
                node["collectSum"] = collect_sum
            _strip_card_previews(node)
            cards = node.get("cards")
            if isinstance(cards, list):
                for card in cards:
                    if not isinstance(card, dict):
                        continue
                    if "value" in card:
                        card["value"] = card_number
                    for key in _money_keys:
                        block = card.get(key)
                        if isinstance(block, dict) and "value" in block:
                            _patch_money_value_dict(block, balance_value)
            return True

        for value in node.values():
            if _patch_first_balance_like_node(value, balance_value, collect_sum, card_number):
                return True
        return False

    if isinstance(node, list):
        for item in node:
            if _patch_first_balance_like_node(item, balance_value, collect_sum, card_number):
                return True
    return False

def response(flow: http.HTTPFlow) -> None:
    url = flow.request.pretty_url

    if not is_bank_flow(flow):
        return
    if not flow.response:
        return
    ensure_response_decoded(flow)
    if not flow.response.text:
        if bank_debug_enabled():
            print(f"[balance] пустой ответ: {url[:120]}")
        return

    if not is_jsonish_response(flow):
        return

    if url_prohibit_proxy_json_mutation(url):
        return

    if flow_statements_spravki_context(flow):
        return

    TEST_DATA = build_balance_test_data()
    if not TEST_DATA:
        return
    
    # ===== ОСНОВНОЙ СПИСОК СЧЕТОВ =====
    # Только первый расчётный счёт с картами — иначе все продукты получали один номер/баланс и плодились «лишние» карты в UI.
    if "moneyAmount" in flow.response.text and "cards" in flow.response.text:
        try:
            data = json.loads(flow.response.text)
            if "payload" in data:
                primary_done = False
                for account in data["payload"]:
                    if not isinstance(account, dict):
                        continue
                    if not primary_done and "cards" in account and account.get("accountType") == "Current":
                        if "moneyAmount" in account and "value" in account["moneyAmount"]:
                            account["moneyAmount"]["value"] = TEST_DATA["new_balance"]
                        if "collectSum" in account:
                            account["collectSum"] = TEST_DATA["new_collect_sum"]
                        _strip_card_previews(account)
                        primary_done = True
                        break
                if primary_done:
                    flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
    
    # ===== ДЕТАЛИ КАРТЫ =====
    if "account_cards" in url:
        try:
            data = json.loads(flow.response.text)
            if "payload" in data and isinstance(data["payload"], list) and len(data["payload"]) > 0:
                card = data["payload"][0]
                if isinstance(card, dict) and "availableBalance" in card:
                    ab = card["availableBalance"]
                    if isinstance(ab, dict) and "value" in ab:
                        ab["value"] = TEST_DATA["new_balance"]
                flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
    
    # ===== ЛЕГКИЙ БАЛАНС (главная строка на mybank) =====
    # Встраиваемый клиент иногда отдаёт путь …/accounts_light без суффикса _ib
    if "accounts_light_ib" in url or (
        "t-bank-app" in url.lower() and "accounts_light" in url.lower()
    ):
        try:
            data = json.loads(flow.response.text)
            if "payload" in data and isinstance(data["payload"], list) and len(data["payload"]) > 0:
                # На главной оставляем одну карточку продукта — без «лишних» дублей в ленте mybank
                pl = data["payload"]
                first = pl[0]
                if len(pl) > 1:
                    data["payload"] = [first]
                if isinstance(first, dict) and "availableBalance" in first:
                    bal = first["availableBalance"]
                    if isinstance(bal, dict) and "value" in bal:
                        bal["value"] = TEST_DATA["new_balance"]
                _strip_card_previews(first)
                flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass

    # ===== Глубокая подмена по JSON-пайплайну (force-URL + условный фолбэк; без /v1/operations) =====
    try:
        _tree_data = json.loads(flow.response.text or "")
    except Exception:
        _tree_data = None
    if isinstance(_tree_data, (dict, list)) and try_apply_balance_tree(
        url=url,
        source="http",
        data=_tree_data,
        test_data=TEST_DATA,
        patch_fn=_patch_first_balance_like_node,
        body_text=flow.response.text or "",
    ):
        flow.response.text = json.dumps(_tree_data, ensure_ascii=False)
        if bank_debug_enabled() and is_force_balance_url(url):
            _u = url.lower()
            print(f"[balance] JSON pipeline (force path): …{_u[max(0, len(_u) - 90):]}")

print("[+] balance.py загружен (динамический конфиг)")