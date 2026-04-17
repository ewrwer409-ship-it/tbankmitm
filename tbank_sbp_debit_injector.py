# -*- coding: utf-8 -*-
"""
Инжектор операции СБП в ленту /api/common/v1/operations (в самый верх).

Учитывает:
  • логотипы и имя банка — из bank_merchants_presets.json (ключ bank_preset);
  • маску карты — из config.json → balance.new_card_number (и при желании поля с первой реальной операции);
  • сумму и тип — из tbank_sbp_injector.json; neutral_gray_transfer=true → Debit + group TRANSFER (как перевод в другой банк, серым);
    иначе show_as_income=true → Credit (без красного «−» в ленте);
  • опционально подкручивает числовые сводки «исходящие/входящие» в том же JSON ответа.

Баланс счёта в приложении по-прежнему задаётся balance.py + config.json (new_balance);
этот скрипт синхронизирует номер карты и сумму/тип операции в ленте.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from urllib.parse import urlparse, parse_qs
from typing import Any, Dict, Optional, Tuple

from mitmproxy import ctx, http

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history

_BASE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_JSON = os.path.join(_BASE, "config.json")
_PRESETS_JSON = os.path.join(_BASE, "bank_merchants_presets.json")
_INJECTOR_JSON = os.path.join(_BASE, "tbank_sbp_injector.json")

_DEFAULT_INJECTOR = {
    "enabled": True,
    "amount": 1250.0,
    "amount_source": "injector",
    "operation_type": "Debit",
    "neutral_gray_transfer": True,
    "show_as_income": True,
    "bank_preset": "alfa",
    "counterparty_name": "Давронбек Ж.",
    "subcategory": "",
    "operation_id": "777777777777",
    "merchant_key": "FAKE_SBP_INJECT_777",
    "time_offset_ms": 2000,
    "inherit_fields_from_first_operation": True,
    "adjust_response_totals": True,
}


def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            x = json.load(f)
        return x if isinstance(x, dict) else None
    except Exception:
        return None


def _injector_settings() -> dict:
    data = _load_json(_INJECTOR_JSON)
    out = dict(_DEFAULT_INJECTOR)
    if data:
        allowed = set(_DEFAULT_INJECTOR.keys())
        out.update({k: v for k, v in data.items() if k in allowed})
    return out


def _op_time_ms(op: dict) -> int:
    best = 0
    ot = op.get("operationTime")
    if isinstance(ot, dict):
        v = ot.get("milliseconds")
        if isinstance(v, (int, float)):
            best = max(best, int(v))
    for blk in (op.get("debitingTime"), op.get("creditingTime")):
        if isinstance(blk, dict):
            v = blk.get("milliseconds")
            if isinstance(v, (int, float)):
                best = max(best, int(v))
    return best


def _sort_payload_operations_newest_first(payload: list) -> None:
    """Строки Credit/Debit: порядок как в history.feed_sort_time_descending() (config history.sort_direction)."""
    if not isinstance(payload, list) or len(payload) < 2:
        return
    idxs = [
        i
        for i, x in enumerate(payload)
        if isinstance(x, dict) and (x.get("type") in ("Credit", "Debit") or x.get("operationType") in ("Credit", "Debit"))
    ]
    if len(idxs) < 2:
        return
    chunk = [payload[i] for i in idxs]

    def key_row(op: dict):
        oid = str(op.get("id") or "")
        manual_tie = 1 if oid.startswith("m_") or str(op.get("merchantKey") or "").startswith("FAKE_SBP") else 0
        return (_op_time_ms(op), manual_tie)

    import history as _hist

    chunk.sort(key=key_row, reverse=_hist.feed_sort_time_descending())
    for j, i in enumerate(idxs):
        payload[i] = chunk[j]


def _resolve_amount(settings: dict) -> float:
    """Сумма из tbank_sbp_injector.json или из config.json → manual.expense / manual.income."""
    src = (settings.get("amount_source") or "injector").strip().lower()
    base_cfg = _load_json(_CONFIG_JSON) or {}
    manual = base_cfg.get("manual") or {}
    if src == "manual_expense":
        v = manual.get("expense")
        if v is not None:
            return float(v)
    elif src == "manual_income":
        v = manual.get("income")
        if v is not None:
            return float(v)
    return float(settings.get("amount") or 0)


def _balance_from_config() -> Tuple[str, str]:
    """Маска карты и запасной номер из панели."""
    cfg = _load_json(_CONFIG_JSON) or {}
    bal = cfg.get("balance") or {}
    card = (bal.get("new_card_number") or "").strip() or "220070******0000"
    card2 = (bal.get("new_card_number2") or "").strip()
    return card, card2


def _preset_brand(preset_key: str) -> Tuple[str, str]:
    raw = _load_json(_PRESETS_JSON) or {}
    key = (preset_key or "alfa").strip().lower()
    block = raw.get(key) or raw.get("alfa") or {}
    m = block.get("merchant") if isinstance(block, dict) else None
    if not isinstance(m, dict):
        return "Альфа-Банк", "https://brands-prod.cdn-tinkoff.ru/general_logo/alfabank.png"
    name = (m.get("name") or "Банк").strip()
    logo = (m.get("logo") or "").strip()
    if not logo:
        logo = "https://brands-prod.cdn-tinkoff.ru/general_logo/alfabank.png"
    return name, logo


def _money_amount(value: float) -> dict:
    return {
        "value": float(value),
        "currency": {"code": 643, "name": "RUB", "strCode": "643"},
    }


def _bump_totals(node: Any, op_type: str, amount: float) -> bool:
    """Увеличить похожие на сводки поля исходящих/входящих в том же ответе."""
    if amount == 0:
        return False
    amt = abs(float(amount))
    skip = ("limit", "fee", "commission", "min", "max", "rate", "percent", "cashback")
    changed = False

    def bump_key(lk: str) -> Optional[str]:
        if any(s in lk for s in skip):
            return None
        if op_type == "Debit" and any(
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
            if not any(b in lk for b in ("incoming", "received", "inbound", "credit", "вход")):
                return "out"
        if op_type == "Credit" and any(
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

    def visit(obj: Any) -> None:
        nonlocal changed
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
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
                        add = amt if kind == "out" else amt
                        v["value"] = round(float(v["value"]) + add, 2)
                        changed = True
                        continue
                visit(v)
        elif isinstance(obj, list):
            for x in obj:
                visit(x)

    visit(node)
    return changed


def _inherit_from_reference(fake: dict, ref: dict) -> None:
    """Копируем реквизиты карты/счёта с реальной операции, если есть."""
    for k in (
        "account",
        "card",
        "ucid",
        "authorizationId",
        "idSourceType",
        "posId",
        "typeSerno",
    ):
        if k in ref and ref[k] is not None:
            fake[k] = copy.deepcopy(ref[k])


def _manual_ops_for_web_feed() -> list[tuple[str, dict]]:
    items = []
    for op_id, op in history.manual_operations.items():
        if op_id in history.hidden_operations:
            continue
        if not history.is_current_month(op.get("date", "")):
            continue
        items.append((op_id, op))
    items.sort(key=lambda x: history.date_str_to_millis(x[1].get("date", "")))
    return items


def _manual_target_from_referer(flow: http.HTTPFlow) -> str:
    try:
        ref = flow.request.headers.get("Referer", "") or ""
        q = parse_qs(urlparse(ref).query)
        mid = (q.get("operationId") or q.get("id") or [""])[0]
        return mid if isinstance(mid, str) and mid.startswith("m_") else ""
    except Exception:
        return ""


def _request_is_from_operations_page(flow: http.HTTPFlow) -> bool:
    try:
        ref = str(flow.request.headers.get("Referer", "") or "").lower()
    except Exception:
        ref = ""
    if not ref:
        return True
    return "/mybank/operations" in ref


def _enrich_manual_web_operation(item: dict, op_id: str, op: dict, ref: dict | None, ms: int) -> dict:
    typ = op.get("type") or "Debit"
    amount = abs(float(op.get("amount") or 0))
    bank_name, logo_url = _preset_brand(str(op.get("bank_preset") or "custom"))
    if op.get("bank"):
        bank_name = str(op.get("bank") or "").strip()
    card_mask, _ = _balance_from_config()

    if isinstance(ref, dict):
        _inherit_from_reference(item, ref)

    item["operationId"] = {"value": op_id, "source": "PrimeAuth"}
    item["authorizationId"] = str(item.get("authorizationId") or op_id)
    item["cardNumber"] = card_mask
    # В requisites/card_credentials блоках нужны account/card/ucid.
    # Если они уже есть (обычно унаследованы от реальной операции в payload),
    # их не трогаем, чтобы `card_credentials` сходился с валидной картой.
    # Prefer ref-операцию (обычно содержит валидные account/card/ucid из реальных данных).
    if isinstance(ref, dict):
        if not item.get("account") and ref.get("account"):
            item["account"] = ref.get("account")
        if not item.get("card") and ref.get("card"):
            item["card"] = ref.get("card")
        if not item.get("ucid") and ref.get("ucid"):
            item["ucid"] = ref.get("ucid")

    # Prefer ref-операцию (обычно содержит валидные account/card/ucid из реальных данных).
    if isinstance(ref, dict):
        if not item.get("account") and ref.get("account"):
            item["account"] = ref.get("account")
        if not item.get("card") and ref.get("card"):
            item["card"] = ref.get("card")
        if not item.get("ucid") and ref.get("ucid"):
            item["ucid"] = ref.get("ucid")

    if not item.get("account"):
        item["account"] = "5860068322"
    if not item.get("card"):
        item["card"] = "383947501"
    if not item.get("ucid"):
        item["ucid"] = "1386102627"
    item["type"] = typ
    item["operationType"] = typ
    item["status"] = item.get("status") or "OK"
    item["accountAmount"] = _money_amount(amount)
    item["signedAmount"] = _money_amount(-amount if typ == "Debit" else amount)
    item["debitAmount"] = _money_amount(amount if typ == "Debit" else 0.0)
    item["creditAmount"] = _money_amount(amount if typ == "Credit" else 0.0)
    item["cashback"] = 0.0
    item["cashbackAmount"] = _money_amount(0.0)
    item["idSourceType"] = item.get("idSourceType") or "Prime"
    item["mcc"] = 0
    item["mccString"] = "0000"
    item["locations"] = item.get("locations") or []
    item["brand"] = {
        "id": "11250",
        "name": bank_name,
        "logo": logo_url,
        "baseColor": "f12e16",
        "fileLink": logo_url,
    }
    item["senderDetails"] = item.get("senderDetails") or ""
    item["subcategory"] = item.get("subcategory") or (op.get("subtitle") or op.get("title") or op.get("description") or "")
    item["loyaltyBonus"] = item.get("loyaltyBonus") or []
    item["loyaltyPayment"] = item.get("loyaltyPayment") or []
    item["loyaltyBonusSummary"] = item.get("loyaltyBonusSummary") or {"amount": 0.0}
    item["offers"] = item.get("offers") or []
    item["cardPresent"] = False
    item["isHce"] = False
    item["isSuspicious"] = False
    item["virtualPaymentType"] = 0
    item["hasStatement"] = True
    item["hasShoppingReceipt"] = False
    item["isDispute"] = False
    item["operationTransferred"] = False
    item["isOffline"] = False
    item["analyticsStatus"] = item.get("analyticsStatus") or "NotSpecified"
    item["isTemplatable"] = False
    item["trancheCreationAllowed"] = False
    item["merchantKey"] = str(item.get("merchantKey") or f"MANUAL_{op_id}")
    item["posId"] = str(item.get("posId") or "585")
    item["typeSerno"] = int(item.get("typeSerno") or 151)
    item["tags"] = item.get("tags") or []
    item["isInner"] = False
    item["isAuto"] = False
    item["merges"] = item.get("merges") or []
    item["documents"] = item.get("documents") or ["Statement"]

    tblock = {"milliseconds": ms}
    item["operationTime"] = {"milliseconds": ms, "seconds": ms / 1000}
    if typ == "Credit":
        item["creditingTime"] = dict(tblock)
        item.pop("debitingTime", None)
        item["additionalInfo"] = [{"fieldName": "Тип перевода", "fieldValue": "Перевод из другого банка"}]
    else:
        item["debitingTime"] = dict(tblock)
        item.pop("creditingTime", None)
        item["additionalInfo"] = [{"fieldName": "Тип перевода", "fieldValue": "Перевод в другой банк"}]

    item["icon"] = logo_url
    return item


class TBankSBPDebitInjector:
    def __init__(self) -> None:
        self.log_file = os.path.join(_BASE, "tbank_sbp_debit_log.txt")
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("=== T-Bank SBP injector ===\n")
            f.write("Настройки: tbank_sbp_injector.json (рядом со скриптом)\n")
            f.write("Логотипы: bank_merchants_presets.json → bank_preset\n")
            f.write("Карта: config.json → balance.new_card_number\n\n")

    def response(self, flow: http.HTTPFlow) -> None:
        if flow.response is None:
            return

        host = flow.request.host.lower()
        path = flow.request.path.lower()

        if not any(h in host for h in ("tbank.ru", "tinkoff.ru", "t-bank-app")):
            return
        ok_common = "/api/common/v1/operations" in path
        ok_embed = (
            "t-bank-app" in host
            and "/v1/operations" in path
            and "operations_histogram" not in path
            and "operations_category" not in path
        )
        if not (ok_common or ok_embed):
            return
        if not _request_is_from_operations_page(flow):
            return

        cfg = _injector_settings()
        if not cfg.get("enabled", True):
            return

        content_type = flow.response.headers.get("content-type", "").lower()
        if "json" not in content_type:
            return

        try:
            text = flow.response.get_text()
            data = json.loads(text)
        except Exception as e:
            ctx.log.error(f"[TBank SBP inject] JSON: {e}")
            return

        if "payload" not in data or not isinstance(data["payload"], list):
            return

        payload: list = data["payload"]
        existing_ids = {
            str(item.get("id"))
            for item in payload
            if isinstance(item, dict) and item.get("id") is not None
        }

        manual_ops = _manual_ops_for_web_feed()
        if manual_ops:
            target_manual_id = _manual_target_from_referer(flow)
            if target_manual_id:
                manual_ops.sort(key=lambda x: 0 if x[0] == target_manual_id else 1)
            manual_map = {str(op_id): op for op_id, op in manual_ops}
            injected = []
            tick_ms = max((_op_time_ms(op) for op in payload if isinstance(op, dict)), default=0)
            fallback_tpl = history.load_fallback_operation_template()
            ref = payload[0] if payload and isinstance(payload[0], dict) else None

            # Если ручные операции уже есть в payload, все равно доводим их до полноценного web-формата.
            existing_manual_changed = False
            for idx, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                if item_id not in manual_map:
                    continue
                try:
                    enriched = _enrich_manual_web_operation(
                        history.overlay_manual_on_template(
                            copy.deepcopy(item),
                            item_id,
                            manual_map[item_id],
                            min_time_ms=None,
                            clamp_to_wall_ms=True,
                        ),
                        item_id,
                        manual_map[item_id],
                        ref,
                        history.operation_time_ms(item),
                    )
                    payload[idx] = enriched
                    existing_manual_changed = True
                except Exception as e:
                    ctx.log.warn(f"[TBank SBP inject] existing manual enrich {item_id}: {e}")

            for op_id, op in manual_ops:
                if str(op_id) in existing_ids:
                    continue
                typ = op.get("type") or "Debit"
                template = history.pick_template_for_type(payload, typ)
                if template is None:
                    template = fallback_tpl
                if template is None:
                    continue
                try:
                    item = history.overlay_manual_on_template(
                        copy.deepcopy(template),
                        op_id,
                        op,
                        min_time_ms=tick_ms,
                        clamp_to_wall_ms=True,
                    )
                    tick_ms = history.operation_time_ms(item)
                    item = _enrich_manual_web_operation(item, op_id, op, ref, tick_ms)
                    injected.append(item)
                    existing_ids.add(str(op_id))
                    _bump_totals(data, typ, float(op.get("amount") or 0))
                except Exception as e:
                    ctx.log.warn(f"[TBank SBP inject] manual overlay {op_id}: {e}")

            if injected or existing_manual_changed:
                data["payload"] = injected + payload
                if target_manual_id:
                    data["payload"].sort(
                        key=lambda row: (
                            0 if isinstance(row, dict) and str(row.get("id") or "") == target_manual_id else 1,
                            -_op_time_ms(row) if isinstance(row, dict) else 0,
                        )
                    )
                history.sort_operations_newest_first(data["payload"])
                try:
                    flow.response.set_text(json.dumps(data, ensure_ascii=False))
                except Exception as e:
                    ctx.log.error(f"[TBank SBP inject] set_text(manual): {e}")
                    return
                ctx.log.info(
                    f"[TBank SBP inject] manual ops updated: injected={len(injected)} existing_changed={1 if existing_manual_changed else 0}"
                )
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(
                        f"manual ops updated: injected={len(injected)} existing_changed={1 if existing_manual_changed else 0} | URL: {flow.request.url}\n\n"
                    )
                return

        op_id = str(cfg.get("operation_id") or "777777777777")
        if payload and isinstance(payload[0], dict) and str(payload[0].get("id")) == op_id:
            return

        if cfg.get("neutral_gray_transfer", True):
            op_type = "Debit"
        elif cfg.get("show_as_income", True):
            op_type = "Credit"
        else:
            op_type = (cfg.get("operation_type") or "Debit").strip()
            if op_type not in ("Debit", "Credit"):
                op_type = "Debit"
        amount = _resolve_amount(cfg)
        if amount <= 0:
            ctx.log.warn("[TBank SBP inject] amount <= 0, пропуск")
            return

        max_ms = 0
        for op in payload:
            if isinstance(op, dict):
                max_ms = max(max_ms, _op_time_ms(op))
        base_ms = int(time.time() * 1000) + int(cfg.get("time_offset_ms") or 0)
        now_ms = max(base_ms, max_ms + 1)
        bank_name, logo_url = _preset_brand(str(cfg.get("bank_preset") or "alfa"))
        card_mask, _ = _balance_from_config()

        title = (cfg.get("counterparty_name") or "Перевод СБП").strip()
        sub = (cfg.get("subcategory") or title).strip()

        fake: Dict[str, Any] = {
            "id": op_id,
            "operationId": {"value": op_id, "source": "PrimeAuth"},
            "isExternalCard": False,
            "account": "5860068322",
            "card": "383947501",
            "ucid": "1386102627",
            "cardNumber": card_mask,
            "authorizationId": op_id,
            "operationTime": {"milliseconds": now_ms},
            "type": op_type,
            "operationType": op_type,
            "status": "OK",
            "amount": _money_amount(amount),
            "accountAmount": _money_amount(amount),
            "signedAmount": _money_amount(-amount if op_type == "Debit" else amount),
            "debitAmount": _money_amount(amount if op_type == "Debit" else 0.0),
            "creditAmount": _money_amount(amount if op_type == "Credit" else 0.0),
            "cashback": 0.0,
            "cashbackAmount": _money_amount(0.0),
            "idSourceType": "Prime",
            "mcc": 0,
            "mccString": "0000",
            "locations": [],
            "description": title,
            "category": {"id": "45", "name": "Другое"},
            "brand": {
                "id": "11250",
                "name": bank_name,
                "logo": logo_url,
                "baseColor": "f12e16",
                "fileLink": logo_url,
            },
            "spendingCategory": {
                "id": "24",
                "name": "Переводы",
                "icon": "transfers-c1",
                "baseColor": "4FC5DF",
            },
            "senderDetails": "",
            "subcategory": sub,
            "loyaltyBonus": [],
            "loyaltyPayment": [],
            "loyaltyBonusSummary": {"amount": 0.0},
            "categoryInfo": {
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
                        "value": title,
                    }
                },
            },
            "group": "TRANSFER",
            "subgroup": {"id": "F1", "name": "Переводы"},
            "offers": [],
            "cardPresent": False,
            "isHce": False,
            "isSuspicious": False,
            "virtualPaymentType": 0,
            "hasStatement": True,
            "hasShoppingReceipt": False,
            "additionalInfo": [
                {
                    "fieldName": "Тип перевода",
                    "fieldValue": "Система быстрых платежей",
                }
            ],
            "isDispute": False,
            "operationTransferred": False,
            "isOffline": False,
            "icon": logo_url,
            "analyticsStatus": "NotSpecified",
            "isTemplatable": False,
            "trancheCreationAllowed": False,
            "merchantKey": str(cfg.get("merchant_key") or "FAKE_SBP_INJECT_777"),
            "posId": "585",
            "typeSerno": 151,
            "tags": [],
            "isInner": False,
            "isAuto": False,
            "merges": [],
            "documents": ["Statement"],
        }

        if cfg.get("inherit_fields_from_first_operation", True) and payload:
            ref = payload[0]
            if isinstance(ref, dict):
                _inherit_from_reference(fake, ref)

        fake["cardNumber"] = card_mask

        tblock = {"milliseconds": now_ms}
        fake["operationTime"] = dict(tblock)
        if op_type == "Credit":
            fake["creditingTime"] = dict(tblock)
            fake.pop("debitingTime", None)
        else:
            fake["debitingTime"] = dict(tblock)
            fake.pop("creditingTime", None)

        if op_type == "Credit":
            fake["additionalInfo"] = [
                {
                    "fieldName": "Тип перевода",
                    "fieldValue": "Входящий перевод по СБП",
                }
            ]

        data["payload"] = [fake] + payload
        _sort_payload_operations_newest_first(data["payload"])

        if cfg.get("adjust_response_totals", True):
            _bump_totals(data, op_type, amount)

        try:
            flow.response.set_text(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            ctx.log.error(f"[TBank SBP inject] set_text: {e}")
            return

        ctx.log.info(
            f"[TBank SBP inject] OK {op_type} {amount} ₽, банк={bank_name}, карта={card_mask[:8]}…"
        )
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(f"OK {op_type} {amount} RUB | preset={cfg.get('bank_preset')} | time_ms={now_ms}\n")
            f.write(f"URL: {flow.request.url}\n\n")


addons = [TBankSBPDebitInjector()]
