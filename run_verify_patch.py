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
            "title": "",
            "name": "",
            "description": "",
            "subtitle": "",
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
            {"label": "РћС‚РїСЂР°РІРёС‚РµР»СЊ", "value": "PLACEHOLDER_SENDER"},
            {"label": "РќРѕРјРµСЂ С‚РµР»РµС„РѕРЅР°", "value": "PLACEHOLDER_PHONE"},
        ],
    }

def row_by_label(requisites, label):
    for r in requisites:
        if r.get("label") == label:
            return r
    return None

def max_depth(obj, d=0):
    best = d
    if isinstance(obj, dict):
        for v in obj.values():
            best = max(best, max_depth(v, d + 1))
    elif isinstance(obj, list):
        for x in obj:
            best = max(best, max_depth(x, d + 1))
    return best

def run_case(man):
    data = make_payload()
    patch(data, man)
    t = data["transfer"]
    return {
        "max_depth_after": max_depth(data),
        "transfer_title": t.get("title"),
        "transfer_name": t.get("name"),
        "productName": t.get("productName"),
        "accountName": t.get("accountName"),
        "cardName": t.get("cardName"),
        "sender_row": row_by_label(data["requisites"], "РћС‚РїСЂР°РІРёС‚РµР»СЊ"),
        "phone_row": row_by_label(data["requisites"], "РќРѕРјРµСЂ С‚РµР»РµС„РѕРЅР°"),
        "all_requisites": list(data["requisites"]),
    }

if __name__ == "__main__":
    kc = run_case({"type": "Credit", "requisite_sender_name": "РЎРІРµС‚Р»Р°РЅР° Р”."})
    kd = run_case({"type": "Debit", "requisite_phone": "+79274062565"})

    def check_credit(t):
        return (
            t["transfer_title"] == "РџРѕРїРѕР»РЅРµРЅРёРµ"
            and t["transfer_name"] == "РџРѕРїРѕР»РЅРµРЅРёРµ"
            and t["productName"] == "Black"
            and t["accountName"] == "Black"
            and t["cardName"] == "Black"
            and t["sender_row"] is not None
            and t["sender_row"].get("value") == "РЎРІРµС‚Р»Р°РЅР° Р”."
        )

    def check_debit(t):
        pr = t["phone_row"]
        return (
            t["transfer_title"] == "РџРµСЂРµРІРѕРґ"
            and t["transfer_name"] == "РџРµСЂРµРІРѕРґ"
            and t["productName"] == "Black"
            and t["accountName"] == "Black"
            and t["cardName"] == "Black"
            and pr is not None
            and pr.get("value") == "+7 927 406-25-65"
        )

    c_ok = check_credit(kc)
    d_ok = check_debit(kd)
    print("max_depth credit", kc["max_depth_after"])
    print("max_depth debit", kd["max_depth_after"])
    print("CREDIT_KEYS")
    print(json.dumps({k: v for k, v in kc.items() if k != "all_requisites"}, ensure_ascii=False, indent=2))
    print(json.dumps(kc["all_requisites"], ensure_ascii=False))
    print("DEBIT_KEYS")
    print(json.dumps({k: v for k, v in kd.items() if k != "all_requisites"}, ensure_ascii=False, indent=2))
    print(json.dumps(kd["all_requisites"], ensure_ascii=False))
    print("CREDIT_OK", c_ok)
    print("DEBIT_OK", d_ok)
    print("RESULT", "PASS" if (c_ok and d_ok) else "FAIL")