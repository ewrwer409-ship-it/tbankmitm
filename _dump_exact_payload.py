import os, sys, json
root = r"C:\Users\dkk150607\Desktop\1111\205E3~1"
os.chdir(root)
sys.path.insert(0, root)
import operation_detail
import run_verify_patch as r
operation_detail._pick_reference_operation = lambda: (None, None)

def dump(man, tag):
    d = r.make_payload()
    operation_detail._patch_manual_detail_semantics(d, man)
    t = d["transfer"]
    c = t.get("card") or {}
    a = t.get("account") or {}
    out = {
        "tag": tag,
        "title": t.get("title"),
        "name": t.get("name"),
        "productName": t.get("productName"),
        "accountName": t.get("accountName"),
        "cardName": t.get("cardName"),
        "card.id": c.get("id"),
        "card.ucid": c.get("ucid"),
        "card.cardNumber": c.get("cardNumber"),
        "top_ucid": t.get("ucid"),
        "top_cardNumber": t.get("cardNumber"),
        "account.id": a.get("id"),
        "requisites": d["requisites"],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

dump({"type": "Credit", "requisite_sender_name": "\u0421\u0432\u0435\u0442\u043b\u0430\u043d\u0430 \u0414."}, "CREDIT exact run_verify payload")
dump({"type": "Debit", "requisite_phone": "+79274062565"}, "DEBIT exact run_verify payload")
