from mitmproxy import http
import json
from datetime import datetime
import re

def pretty_json(text: str) -> str:
    """Красиво форматирует JSON или возвращает обрезанный текст"""
    try:
        data = json.loads(text)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except:
        return text[:600] + ("..." if len(text) > 600 else "")

def is_relevant_url(url: str) -> bool:
    """Проверяем, интересный ли это эндпоинт (платежи, чеки, операции)"""
    url_lower = url.lower()
    keywords = [
        "pay", "payment", "receipt", "kvit", "cheque", "statement", 
        "history", "operations", "details", "document", "tracking", 
        "transaction", "sbp", "success", "confirm", "kvitantsiya"
    ]
    return any(k in url_lower for k in keywords)

def response(flow: http.HTTPFlow) -> None:
    """Логируем ВСЕ ответы от банка (JSON + HTML + другие)"""
    url = flow.request.pretty_url
    if "tbank" not in url.lower() and "tinkoff" not in url.lower():
        return

    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"\n{'═' * 100}")
    print(f"🔹 [RESPONSE] {flow.request.method} {url}")
    print(f"   Status: {flow.response.status_code} | Time: {timestamp}")

    content_type = flow.response.headers.get("content-type", "").lower()

    if "application/json" in content_type:
        print("   📦 JSON RESPONSE")
        print(pretty_json(flow.response.text))
        
        # Автоматический поиск важных ID
        try:
            data_str = flow.response.text.lower()
            keys = ["paymentid", "id", "operationid", "transactionid", "receiptid", 
                   "trackingid", "documentid", "paymentid", "kvit", "cheque"]
            for key in keys:
                if key in data_str:
                    print(f"   → НАЙДЕН КЛЮЧ: {key.upper()}")
        except:
            pass

    elif "text/html" in content_type or "application/xhtml" in content_type:
        print("   📄 HTML RESPONSE (страница чека / успеха / формы)")
        html_preview = flow.response.text[:1200] + ("..." if len(flow.response.text) > 1200 else "")
        print(html_preview)
        
    else:
        print(f"   📌 OTHER ({content_type}) | Body size: {len(flow.response.text or '')} bytes")

    if is_relevant_url(url):
        print("   🔥 РЕЛЕВАНТНЫЙ ЭНДПОИНТ — СКОРЕЕ ВСЁ ЧЕК ИЛИ УСПЕХ!")

def request(flow: http.HTTPFlow) -> None:
    """Логируем запросы (особенно body при /pay)"""
    url = flow.request.pretty_url
    if "tbank" not in url.lower() and "tinkoff" not in url.lower():
        return

    print(f"\n➤ [REQUEST]  {flow.request.method} {url}")

    if flow.request.text:
        try:
            print("   📤 JSON REQUEST BODY:")
            print(pretty_json(flow.request.text))
        except:
            print("   📤 BODY (не JSON):", flow.request.text[:700])

    if is_relevant_url(url):
        print("   🔥 РЕЛЕВАНТНЫЙ ЗАПРОС")

print("🚀 [LOGGER v2.1 — ИСПРАВЛЕНО] Скрипт загружен")
print("   Ошибка ensure_ascii исправлена!")
print("   Теперь лови все: JSON, HTML, запросы на чеки и кнопки «Квитанция»")
print("   Сделай перевод → нажми «Квитанция» → пришли весь лог сюда")