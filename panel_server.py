#!/usr/bin/env python3
"""
Отдельный HTTP сервер для панели управления T-Bank.
Запускается на порту 8083.
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import sys
from datetime import datetime
from urllib.parse import parse_qs, urlparse
import uuid

PORT = 8083
HOST = "0.0.0.0"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import controller
import history

config = controller.config

# Логотипы банков для пресетов
BANK_PRESET_LOGOS = {
    "sbp": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdHJhbnNmZXJzLWMxLnBuZw==",
    "sber": "https://brands-prod.cdn-tinkoff.ru/general_logo/sber.png",
    "tbank": "https://brands-prod.cdn-tinkoff.ru/general_logo/tinkoff-new.png",
    "alfa": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWxmYWJhbmsucG5n",
    "vtb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdnRiYmFuay5wbmc=",
    "psb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJvbXN2eWF6YmFuay5wbmc=",
    "yandex": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veWEtYmFuay5wbmc=",
    "wb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "sovcom": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc292Y29tYmFuay5wbmc=",
    "akbars": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiLnBuZw==",
    "mts": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzZGVuZ2kucG5n",
    "amobayl": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYW1vYmF5bC5wbmc=",
    "eskhata": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZXNraGF0YS1uZXcucG5n",
    "uralsib": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdXJhbHNpYi5wbmc=",
    "fora": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZm9yYWJhbmsucG5n",
    "genbank": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2VuYmFuay1uZXcucG5n",
    "abs_rossiya": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiX3Jvc3NpeWEucG5n",
    "cupis": "https://bms-logo-prod.t-static.ru/general_logo/1cupis-mplat.png",
    "rncb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm5jYi5wbmc=",
    "akcept": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtjZXB0LnBuZw==",
    "domrf": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZG9tcmZiYW5rLnBuZw==",
    "ubrir": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdWJyaXIucG5n",
    "crediteurope": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28va3JlZGl0LWV2cm9wYS1iYW5rLnBuZw==",
    "pochtabank": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcG9jaHRhLWJhbmsucG5n",
    "cifra": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2lmcmEtYmFuay5wbmc=",
    "spitamen": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc3BpdGFtZW5iYW5rLnBuZw==",
    "mkb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWtiLW5ldy0yLnBuZw==",
    "rshb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm9zc2VsaG96YmFuay5wbmc=",
    "primbank": "https://bms-logo-prod.t-static.ru/general_logo/primbank-new.png",
    "primsocbank": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJpbS1zb2MtYmFuay5wbmc=",
    "bankspb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYmFua3NwYi5wbmc=",
    "rocketbank": "https://bms-logo-prod.t-static.ru/general_logo/rocketbank.png",
    "raiffeisen": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcmFpZmZlaXNlbi5wbmc=",
    "mbb": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWJicnUucG5n",
    "centrinvest": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2VudHJpbnZlc3QucG5n",
    "octo": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb2N0by1iYW5rLnBuZw==",
    "yoomoney": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veW9vbW9uZXkucG5n",
    "gazprom": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2F6cHJvbWJhbmsucG5n",
    "mts_bank": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzLWJhbmsucG5n",
    "ozon": "https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png",
    "otp": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb3RwYmFuay1uZXcucG5n",
}

# Читаем HTML панели
HTML_FILE = os.path.join(os.path.dirname(__file__), "panel.html")
HTML_PANEL = None
if os.path.exists(HTML_FILE):
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        HTML_PANEL = f.read()
else:
    # Пробуем из panel_bridge.py
    try:
        from panel_bridge import HTML_PANEL as HB
        HTML_PANEL = HB
    except:
        HTML_PANEL = "<html><body><h1>panel.html не найден</h1></body></html>"

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

        parsed_st = urlparse(self.path)
        if parsed_st.path == "/api/statement/pdf":
            qs = parse_qs(parsed_st.query)
            token = (qs.get("token") or [""])[0].strip()
            dl = (qs.get("download") or ["0"])[0].strip().lower() in ("1", "true", "yes")
            pdf_abs = history.resolve_statement_pdf_token(token)
            if not pdf_abs:
                self.send_response(404)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": "not found or expired"}, ensure_ascii=False).encode("utf-8")
                )
                return
            try:
                pdf_bytes = open(pdf_abs, "rb").read()
            except OSError:
                self.send_response(500)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                return
            disp = 'attachment; filename="statement.pdf"' if dl else 'inline; filename="statement.pdf"'
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Disposition", disp)
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

        if self.path == "/api/statement/generate":
            self.handle_statement_generate(body)
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
            desc = (data.get("description") or "").strip()
            sender_name = (data.get("sender_name") or "").strip()
            requisite_sender_name = (data.get("requisite_sender_name") or sender_name).strip()
            requisite_phone = (data.get("requisite_phone") or data.get("phone") or "").strip()
            bank_preset = (data.get("bank_preset") or "custom").strip().lower() or "custom"
            op_type = "Debit" if direction == "out" else "Credit"
            
            date_str, ms = history.parse_panel_datetime_iso(data.get("datetime"))
            
            op_id = "m_" + uuid.uuid4().hex[:12]
            
            # Получаем логотип для пресета
            logo = BANK_PRESET_LOGOS.get(bank_preset, "") if bank_preset != "custom" else ""
            
            history.manual_operations[op_id] = {
                "id": op_id,
                "date": date_str,
                "amount": amount,
                "type": op_type,
                "description": desc,
                "bank": bank,
                "title": title,
                "subtitle": subtitle,
                "sender_name": sender_name,
                "requisite_sender_name": requisite_sender_name,
                "phone": (data.get("phone") or "").strip(),
                "requisite_phone": requisite_phone,
                "card_number": (data.get("card_number") or "").strip(),
                "bank_preset": bank_preset,
                "logo": logo,
                "operationTime": {"milliseconds": ms, "seconds": ms / 1000},
                "merchant": {"name": bank or title or "Перевод", "logo": logo},
                "counterparty": {"name": bank or title or "Перевод", "logo": logo},
                "amount_data": {"value": amount, "currency": "RUB"},
                "signedAmount": -amount if op_type == "Debit" else amount,
                "debitAmount": amount if op_type == "Debit" else 0,
                "creditAmount": amount if op_type == "Credit" else 0,
            }
            
            history.save_manual_operations()
            
            # Генерация чека
            receipt_path = None
            if op_type == "Debit" and amount > 0:
                try:
                    import func
                    receipt_path = func.generate_operation_receipt(history.manual_operations[op_id])
                    print(f"[panel_server] Чек: {receipt_path}")
                except Exception as e:
                    print(f"[panel_server] Ошибка чека: {e}")
            
            history.sync_panel_income_expense_with_operations()
            self.send_json({"status": "ok", "id": op_id, "receipt_path": receipt_path})
            print(f"[panel_server] Добавлена операция {op_id}")
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
                history.sync_panel_income_expense_with_operations()
                self.send_json({"status": "ok"})
            elif history.remove_fake_transfer_operation(op_id):
                history.sync_panel_income_expense_with_operations()
                self.send_json({"status": "ok"})
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
            # Обновляем логотип если изменился bank_preset
            if "bank_preset" in data:
                new_preset = (data.get("bank_preset") or "custom").strip().lower()
                rec["logo"] = BANK_PRESET_LOGOS.get(new_preset, "") if new_preset != "custom" else ""
                # Обновляем логотип в merchant и counterparty
                if isinstance(rec.get("merchant"), dict):
                    rec["merchant"]["logo"] = rec["logo"]
                if isinstance(rec.get("counterparty"), dict):
                    rec["counterparty"]["logo"] = rec["logo"]
            if data.get("datetime"):
                dstr, op_ms = history.parse_panel_datetime_iso(data["datetime"])
                rec["date"] = dstr
                rec["operationTime"] = {"milliseconds": op_ms, "seconds": op_ms / 1000.0}
            
            history.save_manual_operations()
            history.sync_panel_income_expense_with_operations()
            self.send_json({"status": "ok", "id": op_id})
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
            else:
                self.send_error_json(500, "Ошибка")
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

    def handle_statement_generate(self, body):
        try:
            data = json.loads(body or "{}")
            date_from = (data.get("date_from") or data.get("from") or "").strip()
            date_to = (data.get("date_to") or data.get("to") or "").strip()
            got = history.generate_statement_pdf_for_period(date_from, date_to)
            if not got:
                err = getattr(history, "statement_generation_error", "") or "generation failed"
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": err}, ensure_ascii=False).encode("utf-8")
                )
                return
            pdf_abs, fname = got
            token = history.register_statement_pdf_token(pdf_abs)
            url = f"/api/statement/pdf?token={token}"
            self.send_json_cors({"ok": True, "url": url, "filename": fname, "token": token})
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"))
    
    def random_data(self):
        import random
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
        print(f"[panel_server] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), PanelHandler)
    print(f"[panel_server] Запуск на http://{HOST}:{PORT}")
    print(f"[panel_server] Панель: http://localhost:{PORT}/admin")
    server.serve_forever()
