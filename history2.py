"""
history.py - ОПЕРАЦИИ И СТАТИСТИКА (РАБОЧАЯ ВЕРСИЯ + АВТО-ДОБАВЛЕНИЕ ФЕЙКОВЫХ ПЕРЕВОДОВ)
"""
from mitmproxy import http
import json
from datetime import datetime
from urllib.parse import parse_qs
import os
import re
import requests

# ============================================
# ========== ПОДКЛЮЧЕНИЕ К КОНТРОЛЛЕРУ ==========
try:
    cfg = requests.get("http://localhost:8084/api/config", timeout=0.5).json()
    history_cfg = cfg.get("history", {})
    SHOW_CATEGORIES = history_cfg.get("show_categories", True)
    SORT_DIRECTION = history_cfg.get("sort_direction", "desc")
    FILTER_MONTH = history_cfg.get("filter_month", datetime.now().month)
except:
    SHOW_CATEGORIES = True
    SORT_DIRECTION = "desc"
    FILTER_MONTH = datetime.now().month

operations_cache = {}
hidden_operations = set()
last_sync_time = None
now = datetime.now()
current_year = now.year
current_month = now.month
MAX_OPS = 500

DATA_FILE = "last_transfer.json"   # <-- файл от tbank_unified_transfer.py

def clean_old_ops():
    if len(operations_cache) > MAX_OPS:
        sorted_ops = sorted(operations_cache.items(), key=lambda x: x[1].get("date", ""), reverse=True)
        operations_cache.clear()
        for k, v in sorted_ops[:MAX_OPS]:
            operations_cache[k] = v
        print(f"🧹 Очистка: осталось {len(operations_cache)} операций")

def is_current_month(date_str):
    try:
        match = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', date_str)
        if match:
            day, month, year = map(int, match.groups())
            return year == current_year and month == current_month
    except:
        pass
    return False

def parse_date(date_str):
    try:
        match = re.search(r'(\d{2})\.(\d{2})\.(\d{4}), (\d{2}):(\d{2}):(\d{2})', date_str)
        if match:
            d,m,y,h,min,s = map(int, match.groups())
            return datetime(y,m,d,h,min,s)
    except:
        pass
    return None

def sort_ops(ops_list):
    def get_date(op):
        dt = parse_date(op.get("date",""))
        return dt if dt else datetime.min
    return sorted(ops_list, key=get_date, reverse=True)

# ===================== НОВОЕ: ЗАГРУЗКА ФЕЙКОВЫХ ПЕРЕВОДОВ =====================
def get_fake_expense():
    """Считаем сумму ВСЕХ фейковых переводов (Debit) за текущий месяц из last_transfer.json"""
    if not os.path.exists(DATA_FILE):
        return 0.0
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            fake_history = data.get("fake_history", [])
            total = 0.0
            for op in fake_history:
                # Дата может быть в разных полях (зависит от структуры в tbank_unified_transfer.py)
                date_str = op.get("date_full") or op.get("date") or ""
                if not is_current_month(date_str):
                    continue
                # Сумма может быть dict или int
                amount = op.get("amount")
                if isinstance(amount, dict):
                    amount = amount.get("value", 0)
                total += float(amount or 0)
            return round(total, 2)
    except Exception as e:
        print(f"[history] Ошибка загрузки fake_expense: {e}")
        return 0.0

def calculate_stats():
    """Только реальные операции из кэша (как было раньше)"""
    income = 0.0
    expense = 0.0
    income_count = 0
    expense_count = 0
    for op_id, op in operations_cache.items():
        if op_id in hidden_operations:
            continue
        if is_current_month(op.get("date","")):
            amt = op.get("amount", 0)
            if op.get("type") == "Credit":
                income += amt
                income_count += 1
            elif op.get("type") == "Debit":
                expense += abs(amt)
                expense_count += 1
    return round(income, 2), round(expense, 2), income_count, expense_count

def request(flow: http.HTTPFlow) -> None:
    global operations_cache, hidden_operations, last_sync_time
    url = flow.request.pretty_url
    if flow.request.host == "localhost" and flow.request.port == 8084:
        if flow.request.path == "/api/operations":
            month_ops = []
            for op_id, op in operations_cache.items():
                if is_current_month(op.get("date","")):
                    month_ops.append({
                        "id": op_id,
                        "date": op.get("date",""),
                        "amount": op.get("amount",0),
                        "type": op.get("type",""),
                        "desc": op.get("description",""),
                        "bank": op.get("bank","")
                    })
           
            sorted_ops = sort_ops(month_ops)
            
            # === БАЗОВАЯ СТАТИСТИКА ИЗ КЭША ===
            income, expense, income_count, expense_count = calculate_stats()
            
            # === АВТО-ПРИБАВЛЕНИЕ ФЕЙКОВЫХ ПЕРЕВОДОВ К РАСХОДАМ ===
            fake_expense = get_fake_expense()
            display_expense = expense + fake_expense
            
            response_data = {
                "operations": sorted_ops,
                "hidden": list(hidden_operations),
                "stats": {
                    "income": income,
                    "expense": display_expense,          # <-- ВИЗУАЛЬНО МЕНЯЕМ ТОЛЬКО ДЕЛЬТУ РАСХОДОВ
                    "income_count": income_count,
                    "expense_count": expense_count
                },
                "last_sync": last_sync_time.strftime("%d.%m.%Y %H:%M:%S") if last_sync_time else None
            }
            flow.response = http.Response.make(200, json.dumps(response_data), {"Content-Type": "application/json"})
            print(f"[history] ✅ Отправлено в панель. Расходы = {display_expense:.2f} ₽ (реал {expense:.2f} + фейк {fake_expense:.2f})")
            return

        if flow.request.method == "POST" and flow.request.path == "/api/toggle":
            body = flow.request.text
            op_id = parse_qs(body).get("id", [""])[0]
            if op_id:
                if op_id in hidden_operations:
                    hidden_operations.remove(op_id)
                else:
                    hidden_operations.add(op_id)
                flow.response = http.Response.make(200, "OK")
                return

def response(flow: http.HTTPFlow) -> None:
    global operations_cache, hidden_operations, last_sync_time
    url = flow.request.pretty_url
    if "tbank" not in url and "tinkoff" not in url:
        return
    if not flow.response or not flow.response.text:
        return

    # ========== ОБЫЧНЫЕ ОПЕРАЦИИ ==========
    if ("operations" in url or "history" in url) and 'application/json' in flow.response.headers.get('content-type', ''):
        try:
            data = json.loads(flow.response.text)
            if "payload" in data and isinstance(data["payload"], list):
                ops = data["payload"]
                new_ops = 0
                for op in ops:
                    op_id = op.get("id")
                    if op_id:
                        ts = op.get("operationTime", {}).get("milliseconds")
                        if ts:
                            dt = datetime.fromtimestamp(ts/1000)
                            date_str = dt.strftime("%d.%m.%Y, %H:%M:%S")
                            if op_id not in operations_cache:
                                new_ops += 1
                           
                            operations_cache[op_id] = {
                                "id": op_id,
                                "date": date_str,
                                "amount": op.get("amount", {}).get("value", 0),
                                "type": op.get("type", ""),
                                "description": op.get("description", ""),
                                "bank": op.get("merchant", {}).get("name", "")
                            }
               
                if new_ops:
                    clean_old_ops()
                    last_sync_time = datetime.now()
               
                if hidden_operations:
                    original_count = len(ops)
                    data["payload"] = [op for op in ops if op.get("id") not in hidden_operations]
                    if len(data["payload"]) != original_count:
                        flow.response.text = json.dumps(data, ensure_ascii=False)
                        print(f" 🚫 Скрыто {original_count - len(data['payload'])} операций")
        except Exception as e:
            pass

    # ========== ОПЕРАЦИИ ПО СЧЁТУ ==========
    # Не трогать JSON с экрана «Справки» (/mybank/statements): там тоже «statements» в пути/реферере.
    _ref = (flow.request.headers.get("referer") or "").lower()
    _ul = url.lower()
    _cert_spa = (
        "/mybank/statements" in _ul
        or "mybank%2fstatements" in _ul
        or "/mybank/statements" in _ref
        or "mybank%2fstatements" in _ref
    )
    if (
        "statements" in _ul
        and not _cert_spa
        and 'application/json' in flow.response.headers.get('content-type', '')
    ):
        try:
            data = json.loads(flow.response.text)
            if "payload" in data and isinstance(data["payload"], list):
                ops = data["payload"]
                new_ops = 0
                for op in ops:
                    op_id = op.get("id")
                    if op_id:
                        ts = op.get("operationTime", {}).get("milliseconds")
                        if ts:
                            dt = datetime.fromtimestamp(ts/1000)
                            date_str = dt.strftime("%d.%m.%Y, %H:%M:%S")
                            if op_id not in operations_cache:
                                new_ops += 1
                           
                            operations_cache[op_id] = {
                                "id": op_id,
                                "date": date_str,
                                "amount": op.get("amount", {}).get("value", 0),
                                "type": op.get("type", ""),
                                "description": op.get("description", ""),
                                "bank": op.get("merchant", {}).get("name", "")
                            }
               
                if new_ops:
                    print(f" 🆕 Добавлено {new_ops} операций по счёту")
                    clean_old_ops()
                    last_sync_time = datetime.now()
               
                if hidden_operations:
                    original_count = len(ops)
                    data["payload"] = [op for op in ops if op.get("id") not in hidden_operations]
                    if len(data["payload"]) != original_count:
                        flow.response.text = json.dumps(data, ensure_ascii=False)
                        print(f" 🚫 Скрыто {original_count - len(data['payload'])} операций по счёту")
        except Exception as e:
            pass

    # ========== СТАТИСТИКА (ГИСТОГРАММА) ==========
    # ОСТАВЛЕНО КАК БЫЛО — НЕ ТРОГАЕМ, пусть сама подтягивается
    if "operations_histogram" in url and 'application/json' in flow.response.headers.get('content-type', ''):
        try:
            original = json.loads(flow.response.text)
            income, expense, _, _ = calculate_stats()
           
            if "payload" in original:
                if "earning" in original["payload"] and "summary" in original["payload"]["earning"]:
                    original["payload"]["earning"]["summary"]["value"] = income
               
                if "spending" in original["payload"] and "summary" in original["payload"]["spending"]:
                    original["payload"]["spending"]["summary"]["value"] = expense
                   
                    if "intervals" in original["payload"]["spending"] and original["payload"]["spending"]["intervals"]:
                        for interval in original["payload"]["spending"]["intervals"]:
                            if "aggregated" in interval:
                                for cat in interval["aggregated"]:
                                    if "amount" in cat and "value" in cat["amount"]:
                                        old_val = cat["amount"]["value"]
                                        if old_val > 0 and expense > 0:
                                            cat["amount"]["value"] = round(old_val * (expense / original["payload"]["spending"]["summary"]["value"]), 2)
           
            flow.response.text = json.dumps(original, ensure_ascii=False)
            print(f" ✅ Гистограмма обновлена (без изменений в логике)")
           
        except Exception as e:
            pass

print("[+] history.py загружен (авто-прибавление фейковых переводов к расходам)")
print(" • Дельта расходов обновляется сразу после перевода")
print(" • Гистограмма НЕ трогается — подтягивается сама")
print(" • Только JSON панели меняется — сайт и tbank_unified_transfer.py работают как раньше")