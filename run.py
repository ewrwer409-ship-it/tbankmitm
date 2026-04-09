#!/usr/bin/env python3
"""
Единый сервер для T-Bank Mock Project.
Запускается на порту 8083, включает:
- Панель управления (/admin)
- API операций (/api/operations/*)
- API конфига (/api/config*)
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlparse
import uuid
import random
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import controller
import history

PORT = 8083
HOST = "0.0.0.0"

config = controller.config

# Читаем HTML панели
HTML_FILE = os.path.join(os.path.dirname(__file__), "panel.html")
HTML_PANEL = None
if os.path.exists(HTML_FILE):
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        HTML_PANEL = f.read()
else:
    HTML_PANEL = "<html><body><h1>panel.html не найден</h1><p>Запустите: python -c \"from panel_bridge import HTML_PANEL; open('panel.html','w',encoding='utf-8').write(HTML_PANEL)\"</p></body></html>"

def is_current_month(date_str):
    """Проверка, что операция за текущий месяц ИЛИ создана недавно (менее 24 часов назад)."""
    if not date_str:
        return True
    match = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", date_str)
    if match:
        day, month, year = map(int, match.groups())
        now = datetime.now()
        # Текущий месяц
        if year == now.year and month == now.month:
            return True
        # Или операция создана менее 24 часов назад (для тестов)
        try:
            op_date = datetime(year, month, day)
            delta = abs((now - op_date).days)
            return delta <= 1
        except:
            pass
    return False

def date_str_to_millis(date_str):
    try:
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{2}):(\d{2}):(\d{2})", date_str or "")
        if m:
            d, mo, y, H, M, S = map(int, m.groups())
            dt = datetime(y, mo, d, H, M, S)
            return int(dt.timestamp() * 1000)
    except:
        pass
    return int(datetime.now().timestamp() * 1000)

class PanelHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        p = self.path.split("?", 1)[0]
        if p.startswith("/api/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            return
        self.send_error(404)

    def do_GET(self):
        if self.path == "/admin" or self.path == "/admin/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PANEL.encode("utf-8"))
            return
        
        if self.path == "/api/operations":
            self.send_json(self.get_operations())
            return
        
        if self.path == "/api/config":
            self.send_json(config)
            return

        if self.path.split("?", 1)[0] == "/api/panel_income_expense":
            history.ensure_manual_operations_fresh()
            di, de, _, _ = history.get_panel_chart_display_totals()
            self.send_json_cors({"income": di, "expense": de})
            return

        if self.path.split("?", 1)[0] == "/api/effective_balance":
            base = float((config.get("balance") or {}).get("new_balance") or 0)
            adj = history.compute_manual_balance_adjustment()
            self.send_json_cors({"value": round(base + float(adj), 2)})
            return
        
        if self.path == "/api/balance":
            base = config.get("balance", {}).get("new_balance", 0)
            adj = history.compute_manual_balance_adjustment()
            self.send_json({"base": base, "adjustment": adj, "effective": base + adj})
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/manual_operation_receipt":
            qs = parse_qs(parsed.query)
            op_id = (qs.get("operationId") or qs.get("operation_id") or qs.get("id") or [""])[0].strip()
            pdf_path = history.ensure_operation_receipt_pdf_path(op_id)
            if not pdf_path or not os.path.isfile(pdf_path):
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": "operation not found"}, ensure_ascii=False).encode("utf-8")
                )
                return
            try:
                pdf_bytes = open(pdf_path, "rb").read()
            except OSError:
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Disposition", 'inline; filename="receipt.pdf"')
            self.send_header("Content-Length", str(len(pdf_bytes)))
            self.end_headers()
            self.wfile.write(pdf_bytes)
            return
        
        self.send_error(404, "Not Found")
    
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        
        if self.path == "/api/operations/add":
            self.handle_add_operation(body)
            return
        
        if self.path == "/api/operations/delete":
            self.handle_delete_operation(body)
            return
        
        if self.path == "/api/operations/update":
            self.handle_update_operation(body)
            return
        
        if self.path == "/api/operations/generate_receipt":
            self.handle_generate_receipt(body)
            return
        
        if self.path == "/api/config/save":
            self.handle_save_config(body)
            return
        
        if self.path == "/api/set_manual_stats":
            self.handle_set_manual_stats(body)
            return
        
        if self.path == "/api/random":
            self.send_json(self.random_data())
            return
        
        if self.path == "/api/toggle":
            self.handle_toggle(body)
            return

        if self.path == "/api/hide_all_operations":
            self.handle_hide_all_operations()
            return

        if self.path == "/api/show_all_operations":
            self.handle_show_all_operations()
            return
        
        self.send_error(404, "Not Found")
    
    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def send_json_cors(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    
    def get_operations(self):
        history.ensure_manual_operations_fresh()
        return history.build_operations_api_response()
    
    def handle_add_operation(self, body):
        try:
            data = json.loads(body)
            direction = data.get("direction", "out")
            amount = abs(float(data.get("amount") or 0))
            bank = (data.get("bank") or "").strip()
            title = (data.get("title") or "").strip()
            subtitle = (data.get("subtitle") or "").strip()
            description = (data.get("description") or "").strip()
            sender_name = (data.get("sender_name") or "").strip()
            requisite_sender_name = (data.get("requisite_sender_name") or sender_name).strip()
            bank_preset = (data.get("bank_preset") or "custom").strip().lower() or "custom"
            phone = (data.get("phone") or "").strip()
            requisite_phone = (data.get("requisite_phone") or phone).strip()
            card_number = (data.get("card_number") or "").strip()
            op_type = "Debit" if direction == "out" else "Credit"

            dt_raw = data.get("datetime")
            if dt_raw:
                try:
                    s = str(dt_raw).strip().replace("Z", "")
                    if "+" in s:
                        s = s.split("+", 1)[0]
                    dt = datetime.fromisoformat(s)
                    date_str = dt.strftime("%d.%m.%Y, %H:%M:%S")
                    ms = int(dt.timestamp() * 1000)
                except:
                    date_str = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
                    ms = int(datetime.now().timestamp() * 1000)
            else:
                date_str = datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
                ms = int(datetime.now().timestamp() * 1000)

            op_id = "m_" + uuid.uuid4().hex[:12]

            # Время на 15 секунд в будущем для отображения в начале списка
            future_ms = ms + 15000

            history.manual_operations[op_id] = {
                "id": op_id,
                "date": date_str,
                "amount": amount,
                "type": op_type,
                "description": description,
                "bank": bank,
                "title": title,
                "subtitle": subtitle,
                "sender_name": sender_name,
                "requisite_sender_name": requisite_sender_name,
                "phone": phone,
                "requisite_phone": requisite_phone,
                "card_number": card_number,
                "bank_preset": bank_preset,
                "operationTime": {"milliseconds": future_ms, "seconds": future_ms / 1000},
                # Правильная категория для переводов
                "category": {"name": "Переводы", "id": "transfers"},
                "merchant": {
                    "name": bank or title or "Перевод",
                    "category": {"name": "Переводы", "id": "transfers"}
                },
                "counterparty": {
                    "name": bank or title or "Перевод",
                    "phone": phone,
                    "cardNumber": card_number
                },
                "amount_data": {"value": amount, "currency": "RUB"},
                "signedAmount": -amount if op_type == "Debit" else amount,
                "debitAmount": amount if op_type == "Debit" else 0,
                "creditAmount": amount if op_type == "Credit" else 0,
                # Флаги что это перевод
                "is_transfer": True,
                "transfer_type": "outgoing" if op_type == "Debit" else "incoming",
                # Дополнительные поля для отображения
                "status": "success",
                "processing_status": "success"
            }

            history.save_manual_operations()

            # Автоматическая генерация чека для расходов
            receipt_path = None
            if op_type == "Debit" and amount > 0:
                try:
                    import func
                    receipt_path = func.generate_operation_receipt(history.manual_operations[op_id])
                    print(f"[run.py] ✓ Чек сгенерирован: {receipt_path}")
                    # Пробуем открыть чек
                    try:
                        os.startfile(receipt_path)
                        print(f"[run.py] Чек открыт автоматически")
                    except:
                        pass
                except Exception as e:
                    print(f"[run.py] Ошибка генерации чека: {e}")

            self.send_json({"status": "ok", "id": op_id, "receipt_path": receipt_path})
            print(f"[run.py] Добавлена операция {op_id} ({op_type}, {amount})")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_delete_operation(self, body):
        try:
            data = json.loads(body)
            op_id = data.get("id", "")
            history.ensure_manual_operations_fresh()
            if op_id in history.manual_operations:
                del history.manual_operations[op_id]
                history.hidden_operations.discard(op_id)
                history.save_manual_operations()
                self.send_json({"status": "ok"})
                print(f"[run.py] Удалена операция {op_id}")
            elif history.remove_fake_transfer_operation(op_id):
                self.send_json({"status": "ok"})
                print(f"[run.py] Удалён мок‑перевод {op_id}")
            else:
                self.send_error_json(404, "Не найдена")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_update_operation(self, body):
        try:
            data = json.loads(body)
            op_id = data.get("id", "").strip()
            history.ensure_manual_operations_fresh()
            if not op_id or op_id not in history.manual_operations:
                self.send_error_json(404, "Не найдена")
                return
            
            rec = history.manual_operations[op_id]
            if "amount" in data:
                rec["amount"] = abs(float(data.get("amount") or 0))
            if "direction" in data:
                rec["type"] = "Debit" if data.get("direction") == "out" else "Credit"
            for k in ("title", "subtitle", "description", "bank", "bank_preset", "phone", "card_number", "sender_name", "requisite_phone", "requisite_sender_name"):
                if k in data:
                    rec[k] = (data.get(k) or "").strip() if isinstance(data.get(k), str) else data.get(k)
            if data.get("datetime"):
                try:
                    s = str(data["datetime"]).strip().replace("Z", "")
                    if "+" in s:
                        s = s.split("+", 1)[0]
                    dt = datetime.fromisoformat(s)
                    rec["date"] = dt.strftime("%d.%m.%Y, %H:%M:%S")
                except:
                    pass
            
            history.save_manual_operations()
            self.send_json({"status": "ok", "id": op_id})
            print(f"[run.py] Обновлена операция {op_id}")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_generate_receipt(self, body):
        try:
            data = json.loads(body)
            op_id = data.get("id", "").strip()
            history.ensure_manual_operations_fresh()
            if op_id not in history.manual_operations:
                self.send_error_json(404, "Не найдена")
                return
            
            op_data = history.manual_operations[op_id]
            
            import transfer
            receipt_path = transfer.generate_receipt_for_manual_op(op_data)
            
            if receipt_path:
                self.send_json({"status": "ok", "receipt_path": receipt_path})
                print(f"[run.py] Чек: {receipt_path}")
            else:
                self.send_error_json(500, "Ошибка генерации")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_save_config(self, body):
        try:
            new_config = json.loads(body)
            for key, value in new_config.items():
                if key in controller.config and isinstance(controller.config[key], dict) and isinstance(value, dict):
                    controller.config[key].update(value)
                else:
                    controller.config[key] = value
            controller.save_config()
            self.send_json({"status": "ok"})
            print(f"[run.py] Конфиг сохранён")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_set_manual_stats(self, body):
        try:
            data = json.loads(body)
            if not (
                'income' in data
                or 'expense' in data
                or 'histogram_sync_with_operations' in data
            ):
                self.send_error_json(400, "bad request")
                return
            if 'manual' not in controller.config:
                controller.config['manual'] = {}
            did_manual_amount = False
            if 'income' in data:
                if data['income'] is not None:
                    controller.config['manual']['income'] = data['income']
                    did_manual_amount = True
                else:
                    controller.config['manual'].pop('income', None)
            if 'expense' in data:
                if data['expense'] is not None:
                    controller.config['manual']['expense'] = data['expense']
                    did_manual_amount = True
                else:
                    controller.config['manual'].pop('expense', None)
            if 'histogram_sync_with_operations' in data:
                v = data['histogram_sync_with_operations']
                if v is None:
                    controller.config['manual'].pop('histogram_sync_with_operations', None)
                else:
                    controller.config['manual']['histogram_sync_with_operations'] = bool(v)
            elif did_manual_amount:
                controller.config['manual']['histogram_sync_with_operations'] = False
            controller.save_config()
            self.send_json({"status": "ok"})
            print(f"[run.py] Статистика обновлена: {data}")
        except Exception as e:
            self.send_error_json(400, str(e))
    
    def handle_toggle(self, body):
        params = parse_qs(body)
        op_id = params.get("id", [""])[0]
        history.ensure_manual_operations_fresh()
        if op_id and (
            op_id in history.operations_cache
            or op_id in history.manual_operations
            or history.op_id_in_fake_history_files(op_id)
        ):
            if op_id in history.hidden_operations:
                history.hidden_operations.remove(op_id)
            else:
                history.hidden_operations.add(op_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
            print(f"[run.py] Toggle: {op_id[:8]}")
        else:
            self.send_error(400, "Bad Request")

    def handle_hide_all_operations(self):
        history.ensure_manual_operations_fresh()
        history.hidden_operations.update(history.operations_cache.keys())
        history.hidden_operations.update(history.manual_operations.keys())
        for p in history._last_transfer_json_paths():
            if not os.path.isfile(p):
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    td = json.load(f)
                for fo in td.get("fake_history") or []:
                    if isinstance(fo, dict) and fo.get("id"):
                        history.hidden_operations.add(str(fo.get("id")))
            except Exception:
                pass
        self.send_json_cors({"status": "ok", "count": len(history.hidden_operations)})

    def handle_show_all_operations(self):
        history.hidden_operations.clear()
        self.send_json_cors({"status": "ok"})
    
    def random_data(self):
        first_names = ["Иван", "Александр", "Дмитрий", "Сергей", "Андрей"]
        last_names = ["Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов"]
        idx = random.randint(0, 4)
        return {
            "name": {
                "first_name": first_names[idx],
                "last_name": last_names[idx],
                "full_name": f"{last_names[idx]} {first_names[idx]} Иванович",
                "phone": f"+7{random.randint(900, 999)}{random.randint(100, 999)}{random.randint(10, 99)}{random.randint(10, 99)}"
            },
            "balance": {
                "new_balance": round(random.uniform(10000, 100000), 2),
                "new_card_number": f"{random.randint(1000, 9999)}******{random.randint(1000, 9999)}"
            }
        }
    
    def send_error_json(self, code, message):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))
    
    def log_message(self, format, *args):
        print(f"[run.py] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), PanelHandler)
    print(f"=" * 60)
    print(f"T-Bank Mock Server запущен!")
    print(f"=" * 60)
    print(f"Панель управления: http://localhost:{PORT}/admin")
    print(f"API операций: http://localhost:{PORT}/api/operations")
    print(f"API конфига: http://localhost:{PORT}/api/config")
    print(f"Подсказка: в config.json задайте \"panel_http_port\": {PORT} или \"panel_fetch_origin\": \"http://<ваш_IP>:{PORT}\" — иначе в приложении не подтянутся траты/баланс с этой панели.")
    print(f"=" * 60)
    history.ensure_manual_operations_fresh()
    print(f"Загружено операций: {len(history.manual_operations)}")
    income, expense, inc, exp = history.calculate_stats()
    print(f"Статистика за месяц: доходы={income}, расходы={expense}")
    print(f"=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[run.py] Остановка...")
        server.shutdown()
