"""
Ориентир на JSON и тип канала (HTTP / WebSocket), а не на разрозненные проверки в каждом аддоне.

- Классификация URL: где разрешён глубокий обход «денежного» дерева, где запрещён (лента операций и т.п.).
- Единая точка: ``try_apply_balance_tree`` — вызывается из ``balance.response`` и ``ws_bank_push_patch``.

Расширять списки и эвристику здесь; аддоны только парсят JSON и вызывают вход.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

FlowSource = Literal["http", "websocket"]

# Жёстко патчим JSON по подстроке пути. «/v1/now» (api.tinkoff.ru / t-bank) часто отдаёт
# краткое состояние/суммы для шапки — без этого экран «крутит» обновление, а цифры с /accounts_light не подхватываются.
_FORCE_BALANCE_PATH_SUBSTR: tuple[str, ...] = (
    "account_details",
    "full_debt_amount",
    "credit/collection_info",
    "/v1/account_details",
    "/v1/now",
    "operation/info",
    "operation_detail",
    "operation/view",
    "cashflow",
    "cash-flow",
    "money-session",
)


def is_force_balance_url(url: str) -> bool:
    u = (url or "").lower()
    return any(s in u for s in _FORCE_BALANCE_PATH_SUBSTR)


def is_tbank_embedded_url(url: str) -> bool:
    return "t-bank-app" in (url or "").lower()


def is_balance_tree_forbidden_url(url: str) -> bool:
    """
    Глубокий walk меняет любые moneyAmount / availableBalance в дереве — на ленте операций это сломает суммы постов.
    Гистограммы и категории пусть крутит panel_bridge / отдельная логика.
    """
    u = (url or "").lower()
    if "operations_histogram" in u or "operations_category" in u:
        return True
    if "/v1/operations" not in u:
        return False
    # …/v1/operations?… или …/v1/operations — лента; не …/v1/operations_histogram
    if "/v1/operations_" in u:
        return False
    return True


def body_suggests_balance_fields(body: str, url: str) -> bool:
    t = body or ""
    if "availableBalance" in t or "moneyAmount" in t:
        return True
    uq = (url or "").lower()
    if not (
        is_tbank_embedded_url(url)
        or "tinkoff" in uq
        or "tbank" in uq
        or "t-bank" in uq
    ):
        return False
    return any(
        x in t
        for x in (
            '"balance"',
            '"accountBalance"',
            '"available_balance"',
            '"factBalance"',
            '"currentBalance"',
            '"totalBalance"',
            '"walletBalance"',
            '"mainBalance"',
        )
    )


def try_apply_balance_tree(
    *,
    url: str,
    source: FlowSource,
    data: Any,
    test_data: dict[str, Any],
    patch_fn: Callable[..., bool],
    body_text: str | None = None,
) -> bool:
    """
    ``patch_fn`` — как ``balance._patch_first_balance_like_node(data, balance, collect, card)``.

    ``body_text`` — сырой текст ответа HTTP; для WS можно не передавать (возьмём сериализацию data).
    """
    if is_balance_tree_forbidden_url(url):
        return False

    bal = test_data["new_balance"]
    coll = test_data["new_collect_sum"]
    card = test_data["new_card_number"]

    if is_force_balance_url(url):
        return bool(patch_fn(data, bal, coll, card))

    hint: str
    if body_text is not None:
        hint = body_text
    else:
        try:
            import json as _json

            hint = _json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else ""
        except Exception:
            hint = ""

    if source == "http":
        if body_suggests_balance_fields(hint, url):
            return bool(patch_fn(data, bal, coll, card))
        return False

    if source == "websocket":
        if body_suggests_balance_fields(hint, url):
            return bool(patch_fn(data, bal, coll, card))
        return False

    return False
