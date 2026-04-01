import json
import os

R = lambda *c: "".join(chr(x) for x in c)
L_SENDER = R(0x041e, 0x0442, 0x043f, 0x0440, 0x0430, 0x0432, 0x0438, 0x0442, 0x0435, 0x043b, 0x044c)
L_PHONE = R(0x041d, 0x043e, 0x043c, 0x0435, 0x0440, 0x0020, 0x0442, 0x0435, 0x043b, 0x0435, 0x0444, 0x043e, 0x043d, 0x0430)
POP = R(0x041f, 0x043e, 0x043f, 0x043e, 0x043b, 0x043d, 0x0435, 0x043d, 0x0438, 0x0435)
PER = R(0x041f, 0x0435, 0x0440, 0x0435, 0x0432, 0x043e, 0x0434)
SV = R(0x0421, 0x0432, 0x0435, 0x0442, 0x043b, 0x0430, 0x043d, 0x0430) + " " + R(0x0414) + "."
PROJ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "205E3~1")
path = os.path.join(PROJ, "_verify_strict_payload.py")
body = f"""# -*- coding: utf-8 -*-
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import operation_detail

patch = operation_detail._patch_manual_detail_semantics

L_SENDER = {json.dumps(L_SENDER, ensure_ascii=False)}
L_PHONE = {json.dumps(L_PHONE, ensure_ascii=False)}
POP = {json.dumps(POP, ensure_ascii=False)}
PER = {json.dumps(PER, ensure_ascii=False)}
SV = {json.dumps(SV, ensure_ascii=False)}


def base_transfer():
    return {{
        "title": "",
        "name": "",
        "description": "",
        "subtitle": "",
        "productName": "PLACEHOLDER_PRODUCT",
        "accountName": "PLACEHOLDER_ACCOUNT",
        "cardName": "PLACEHOLDER_CARD",
        "moneyAmount": {{"value": 0.0}},
        "balance": {{"value": 0.0}},
        "availableBalance": {{"value": 0.0}},
        "accountBalance": {{"value": 0.0}},
        "ucid": "",
        "account": {{"id": ""}},
        "card": {{"id": "", "ucid": "", "cardNumber": ""}},
        "cardNumber": "",
        "requisites": [
            {{"label": L_SENDER, "value": "PLACEHOLDER_SENDER"}},
            {{"label": L_PHONE, "value": "PLACEHOLDER_PHONE"}},
        ],
    }}


def check_transfer(node, exp_title, exp_pn, exp_an, exp_cn, case_name):
    errs = []
    if (node.get("title") or "") != exp_title:
        errs.append(f"{{case_name}}: title expected {{exp_title!r}} got {{node.get('title')!r}}")
    if (node.get("name") or "") != exp_title:
        errs.append(f"{{case_name}}: name expected {{exp_title!r}} got {{node.get('name')!r}}")
    if node.get("productName") != exp_pn:
        errs.append(f"{{case_name}}: productName expected {{exp_pn!r}} got {{node.get('productName')!r}}")
    if node.get("accountName") != exp_an:
        errs.append(f"{{case_name}}: accountName expected {{exp_an!r}} got {{node.get('accountName')!r}}")
    if node.get("cardName") != exp_cn:
        errs.append(f"{{case_name}}: cardName expected {{exp_cn!r}} got {{node.get('cardName')!r}}")
    if node.get("description") != "Black":
        errs.append(f"{{case_name}}: description expected 'Black' got {{node.get('description')!r}}")
    if node.get("subtitle") != "Black":
        errs.append(f"{{case_name}}: subtitle expected 'Black' got {{node.get('subtitle')!r}}")
    return errs


def main():
    o = copy.deepcopy(base_transfer())
    man_c = {{"type": "Credit", "requisite_sender_name": SV}}
    patch(o, man_c)
    errs_c = check_transfer(o, POP, "Black", "Black", "Black", "Credit")
    req = o.get("requisites") or []
    if not req or req[0].get("label") != L_SENDER:
        errs_c.append(f"Credit: first requisite label mismatch, got {{req!r}}")
    if not req or req[0].get("value") != SV:
        errs_c.append(f"Credit: sender row value mismatch, got {{req!r}}")

    o2 = copy.deepcopy(base_transfer())
    man_d = {{"type": "Debit", "requisite_phone": "+79274062565"}}
    patch(o2, man_d)
    errs_d = check_transfer(o2, PER, "Black", "Black", "Black", "Debit")
    want_phone = "+7 927 406-25-65"
    req2 = o2.get("requisites") or []
    phone_rows = [r for r in req2 if (r.get("label") or "") == L_PHONE]
    if not any(r.get("value") == want_phone for r in phone_rows):
        errs_d.append(f"Debit: no phone row with formatted value, got {{req2!r}}")
    if len(req2) >= 2 and req2[1].get("value") != want_phone:
        errs_d.append(
            f"Debit: requisites[1].value expected {{want_phone!r}} got {{req2[1].get('value')!r}}"
        )

    first_debit = req2[0] if req2 else {{}}
    debit_sender_relabeled = first_debit.get("label") == L_PHONE

    print("=== Credit requisites ===")
    print(o.get("requisites"))
    print("=== Debit requisites ===")
    print(o2.get("requisites"))
    print("=== Debit: first row ===")
    print(first_debit)
    print("debit_sender_row_relabeled_to_phone:", debit_sender_relabeled)

    all_errs = errs_c + errs_d
    if all_errs:
        print("FAIL")
        for e in all_errs:
            print(" ", e)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
open(path, "w", encoding="utf-8").write(body)
print("wrote", path)
