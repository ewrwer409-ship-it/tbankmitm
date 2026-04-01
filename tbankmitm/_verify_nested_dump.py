# -*- coding: utf-8 -*-
import os, sys, json
root = os.path.dirname(os.path.abspath(__file__))
os.chdir(root)
sys.path.insert(0, root)
import operation_detail
operation_detail._pick_reference_operation = lambda: (None, None)
patch = operation_detail._patch_manual_detail_semantics

def make_payload():
    return {
        "transfer": {
            "title": "", "name": "", "description": "", "subtitle": "",
            "productName": "PLACEHOLDER_PRODUCT",
            "accountName": "PLACEHOLDER_ACCOUNT",
            "cardName": "PLACEHOLDER_CARD",
            "moneyAmount": {"value": 1.0, "currency": "RUB"},
            "balance": {"value": 1.0, "currency": "RUB"},
            "availableBalance": {"value": 1.0, "currency": "RUB"},
            "accountBalance": {"value": 1.0, "currency": "RUB"},
            "ucid": "",
            "account": {"id": ""},
            "card": {"id": "", "ucid": "", "cardNumber": ""},
            "cardNumber": "",
        },
        "requisites": [
            {"label": "\u041e\u0442\u043f\u0440\u0430\u0432\u0438\u0442\u0435\u043b\u044c", "value": "PLACEHOLDER_SENDER"},
            {"label": "\u041d\u043e\u043c\u0435\u0440 \u0442\u0435\u043b\u0435\u0444\u043e\u043d\u0430", "value": "PLACEHOLDER_PHONE"},
        ],
    }

def run(man):
    data = make_payload()
    patch(data, man)
    t = data["transfer"]
    card = t.get("card") or {}
    acct = t.get("account") or {}
    return {
        "title": t.get("title"), "name": t.get("name"),
        "productName": t.get("productName"), "accountName": t.get("accountName"), "cardName": t.get("cardName"),
        "card.id": card.get("id"), "card.ucid": card.get("ucid"), "card.cardNumber": card.get("cardNumber"),
        "top_cardNumber": t.get("cardNumber"), "top_ucid": t.get("ucid"),
        "account.id": acct.get("id"),
        "requisites": data["requisites"],
    }

kc = run({"type": "Credit", "requisite_sender_name": "\u0421\u0432\u0435\u0442\u043b\u0430\u043d\u0430 \u0414."})
kd = run({"type": "Debit", "requisite_phone": "+79274062565"})
print("=== CREDIT UTF8 labels ===")
print(json.dumps(kc, ensure_ascii=False, indent=2))
print("=== DEBIT ===")
print(json.dumps(kd, ensure_ascii=False, indent=2))
