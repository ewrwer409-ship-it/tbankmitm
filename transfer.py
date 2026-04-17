from mitmproxy import http
import json
import random
import re
from datetime import datetime, timedelta
import os
import sys
import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bank_filter import is_bank_flow, ensure_response_decoded, is_jsonish_response
import history as history_mod
import controller as ctrl
from pathlib import Path
import subprocess
import urllib.parse
import time
from urllib.parse import unquote

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_SCRIPT_DIR, "config.json")
DATA_FILE = os.path.join(_SCRIPT_DIR, "last_transfer.json")

# ====================== ПОЛНАЯ ТАБЛИЦА МАППИНГА bankMemberId → Название ======================
BANK_MAP = {
    "1": "Сбербанк",
    "2": "Альфа-Банк",
    "3": "ВТБ",
    "4": "Райффайзенбанк",
    "5": "Открытие",
    "6": "РНКБ Банк",
    "7": "Газпромбанк",
    "8": "Золотая Корона",
    "9": "Почта Банк",
    "10": "Совкомбанк",
    "11": "Промсвязьбанк",
    "12": "Банк Санкт-Петербург",
    "13": "Хоум Банк",
    "14": "МТС Банк",
    "15": "Ак Барс",
    "16": "МКБ (Московский Кредитный Банк)",
    "17": "Россельхозбанк",
    "18": "ЮниКредит Банк",
    "19": "ВБРР",
    "20": "АБ Россия",
    "21": "Банк Дом.РФ",
    "22": "Банк НОВИКОМ (НОВИКОМБАНК)",
    "23": "Уралсиб",
    "24": "Русский Стандарт",
    "25": "УБРиР",
    "26": "Абсолют Банк",
    "27": "ЮMoney",
    "28": "Банк Зенит",
    "29": "Транскапиталбанк",
    "30": "Цифра Банк",
    "31": "Драйв Клик Банк",
    "32": "Локо-Банк",
    "33": "Ренессанс Кредит",
    "34": "ВУЗ-Банк",
    "35": "ОТП Банк",
    "36": "Банк Авангард",
    "37": "Металлинвестбанк",
    "38": "Азиатско-Тихоокеанский Банк",
    "39": "Сургутнефтегазбанк",
    "40": "Экспобанк",
    "41": "Кредит Европа Банк",
    "42": "Таврический Банк",
    "43": "Банк Аверс",
    "44": "Кубань Кредит",
    "45": "Центр-Инвест",
    "46": "Банк Финсервис",
    "47": "ББР Банк",
    "48": "Банк Центрокредит",
    "49": "Банк Синара",
    "50": "Ингосстрах Банк",
    "51": "SBI Bank",
    "52": "Примсоцбанк",
    "53": "Газэнергобанк",
    "54": "Меткомбанк",
    "55": "Банк Интеза",
    "56": "БКС Банк",
    "57": "СДМ-Банк",
    "58": "Тойота Банк",
    "59": "Банк Левобережный",
    "60": "Севергазбанк",
    "61": "Солидарность",
    "62": "Банк Объединенный Капитал",
    "63": "Челябинвестбанк",
    "64": "Международный Финансовый Клуб",
    "65": "Фора Банк",
    "66": "Челиндбанк",
    "67": "Генбанк",
    "68": "Банк Держава",
    "69": "Энерготрансбанк",
    "70": "Дальневосточный Банк",
    "71": "Кредит Урал Банк",
    "72": "Аресбанк",
    "73": "Банк Приморье",
    "74": "Тимер Банк",
    "75": "Быстробанк",
    "76": "ПСКБ",
    "77": "MC Bank Rus",
    "78": "Интерпрогрессбанк",
    "79": "НС Банк",
    "80": "Банк Национальный Стандарт",
    "81": "Ланта Банк",
    "82": "Алмазэргиэнбанк",
    "83": "Банк Хлынов",
    "84": "Росдорбанк",
    "85": "Модульбанк",
    "86": "НБД-Банк",
    "87": "Акибанк",
    "88": "Урал ФД",
    "89": "Инбанк",
    "90": "Экономбанк",
    "91": "Москоммерцбанк",
    "92": "Татсоцбанк",
    "93": "Акцепт",
    "94": "НК Банк",
    "95": "Энергобанк",
    "96": "Норвик Банк",
    "97": "Агропромкредит",
    "98": "РЕСО Кредит",
    "99": "Реалист Банк",
    "100": "Морской Банк",
    "101": "Банк Александровский",
    "102": "Прио Внешторгбанк",
    "103": "Тольяттихимбанк",
    "104": "Кошелев-Банк",
    "105": "Пойдём!",
    "106": "Ишбанк",
    "107": "Банк Оренбург",
    "108": "Еврофинанс Моснарбанк",
    "109": "Банк ПТБ",
    "110": "Алеф-Банк",
    "111": "Развитие-Столица",
    "112": "Форштадт",
    "113": "Автоторгбанк",
    "114": "Банк Раунд",
    "115": "Руснарбанк",
    "116": "Нико-Банк",
    "117": "Датабанк",
    "118": "БЖФ Банк",
    "119": "Нацинвестпромбанк",
    "120": "Банк Казани",
    "121": "ЮГ-Инвестбанк",
    "122": "Пробанк",
    "123": "Русский Универсальный Банк",
    "124": "Банк Снежинский",
    "125": "Финам Банк",
    "126": "Екатеринбург Банк",
    "127": "Мир Бизнес Банк",
    "128": "Банк МБА-Москва",
    "129": "Итуруп Банк",
    "130": "Банк Мир Привилегий",
    "131": "Банк Ростфинанс",
    "132": "Национальный Резервный Банк",
    "133": "Углеметбанк",
    "134": "Новобанк",
    "135": "Солид Банк",
    "136": "Томскпромстройбанк",
    "137": "Хакасский Муниципальный Банк",
    "138": "Трансстройбанк",
    "139": "Роял Кредит Банк",
    "140": "Сибсоцбанк",
    "141": "Кузнецкбизнесбанк",
    "142": "Банк Агророс",
    "143": "Белгородсоцбанк",
    "144": "Гута Банк",
    "145": "Славия Банк",
    "146": "Северный Народный Банк",
    "147": "Газтрансбанк",
    "148": "Земский Банк",
    "149": "Банк Новый Век",
    "150": "Венецбанк",
    "151": "Петербургский Городской Банк",
    "152": "Енисейский объединенный банк",
    "153": "Индустриальный Сберегательный Банк",
    "154": "Стройлесбанк",
    "155": "Крокус Банк",
    "156": "Банк Вологжанин",
    "157": "Авто Финанс Банк",
    "158": "Бланк банк",
    "159": "КБ Долинск",
    "160": "Нокссбанк",
    "161": "Владбизнесбанк",
    "162": "Братский Народный Банк",
    "163": "Кубаньторгбанк",
    "164": "Банк Кремлевский",
    "165": "Банк Заречье",
    "166": "Банк \"Элита\"",
    "167": "Московский Коммерческий Банк",
    "168": "Первый Дортрансбанк",
    "169": "Уралфинанс",
    "170": "Синко-Банк",
    "171": "Уралпромбанк",
    "172": "Тендер-Банк",
    "173": "Банк Москва-Сити",
    "174": "Юнистрим",
    "175": "Первый Инвестиционный Банк",
    "176": "Социум Банк",
    "177": "Внешфинбанк",
    "178": "Банк Йошкар-Ола",
    "179": "ИК Банк",
    "180": "Банк Саратов",
    "181": "VK Pay - ВК Платёжные решения",
    "182": "Яндекс Банк",
    "183": "МОБИ.Деньги",
    "184": "Вайлдберриз Банк",
    "185": "НКО Монета",
    "186": "Озон Банк (Ozon)",
    "187": "Элплат",
    "188": "Мобильная карта",
    "189": "Хайс",
    "190": "Точка Банк",
    "191": "Банк Живаго",
    "192": "Яринтербанк",
    "193": "СтавропольПромСтройБанк",
    "194": "МТС Деньги (ЭКСИ-Банк)",
    "195": "Первоуральскбанк",
    "196": "Авито Кошелек (Платежный конструктор)",
    "197": "ФИНСТАР БАНК",
    "198": "Рокетбанк",
    "199": "Банк Оранжевый",
    "200": "Банк Кузнецкий",
    "201": "ЦМРБанк",
    "202": "Свой Банк",
    "203": "Банк РСИ",
    "205": "Банк ЧБРР",
    # Добавь свои ID, если нужно
}

BANK_LOGO = {
    "Сбербанк": "https://brands-prod.cdn-tinkoff.ru/general_logo/sber.png",
    "Т-Банк": "https://brands-prod.cdn-tinkoff.ru/general_logo/tinkoff-new.png",
    "Альфа-Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWxmYWJhbmsucG5n",
    "ВТБ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdnRiYmFuay5wbmc=",
    "Яндекс Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veWEtYmFuay5wbmc=",
    "Вайлдберриз Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "Wildberries (Вайлдберриз Банк)": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "Совкомбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc292Y29tYmFuay5wbmc=",
    "ЮMoney": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veW9vbW9uZXkucG5n",
    "Газпромбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2F6cHJvbWJhbmsucG5n",
    "Райффайзенбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcmFpZmZlaXNlbi5wbmc=",
    "Озон Банк (Ozon)": "https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png",
    "Озон Банк": "https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png",
    "МТС Деньги (ЭКСИ-Банк)": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzZGVuZ2kucG5n",
}

_last_fake_ts = 0.0
_last_fake_op_id = None
_last_fake_hash = ""
_fake_payment_done = False


def get_bank_logo(bank_name):
    if not bank_name:
        return BANK_LOGO.get("Т-Банк")
    if bank_name in BANK_LOGO:
        return BANK_LOGO[bank_name]
    for key, url in BANK_LOGO.items():
        if key.lower() in bank_name.lower() or bank_name.lower() in key.lower():
            return url
    return BANK_LOGO.get("Т-Банк")


def clean_bank_name(name):
    if not name:
        return "Т-Банк"
    name = str(name).strip()
    if "Озон" in name:
        return "Озон Банк (Ozon)"
    if any(x in name for x in ("Wildberries", "Вайлдберриз", "WB")):
        return "Wildberries (Вайлдберриз Банк)"
    return name


def load_config():
    if Path(CONFIG_FILE).exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

config = load_config()

def load_data():
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_sender_name(full_name):
    if not full_name:
        return "Клиент Т-Банка"
    parts = [p.strip() for p in full_name.split() if p.strip()]
    if len(parts) == 3:
        return f"{parts[1]} {parts[0]}"  # Имя + Фамилия
    if len(parts) == 2:
        return f"{parts[0]} {parts[1]}"
    return full_name or "Клиент Т-Банка"

transfer_data = load_data() or {
    "amount": 0.0,
    "transaction_id": None,
    "date_full": None,
    "receiver_phone": None,
    "receiver_name": None,
    "sender_name": clean_sender_name(config.get("name", {}).get("full_name", "Клиент Т-Банка")),
    "sender_account": config.get("reki", {}).get("account", "408178100001****7576"),
    "bank_receiver": "Т-Банк",
    "bank_logo": None,
    "merchant_name": None,
    "merchant_logo": None,
    "is_merchant_payment": False,
    "kvit_number": None,
    "sbp_operation_id": None,
    "last_pdf_path": None,
    "payment_id": None,
    "fake_history": [],
}
if not isinstance(transfer_data.get("fake_history"), list):
    transfer_data["fake_history"] = []
for _k, _v in (
    ("bank_logo", None),
    ("merchant_name", None),
    ("merchant_logo", None),
    ("is_merchant_payment", False),
):
    transfer_data.setdefault(_k, _v)
fake_history = transfer_data["fake_history"]

def generate_id(length=16):
    return ''.join(random.choices('0123456789', k=length))

def format_phone(phone):
    if not phone:
        return "+7 (XXX) XXX-XX-XX"
    digits = ''.join(filter(str.isdigit, str(phone)))
    if len(digits) == 11 and digits.startswith('7'):
        digits = digits[1:]
    if len(digits) != 10:
        return "+7 (XXX) XXX-XX-XX"
    return f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:]}"

def format_account(account):
    """Тот же вид счёта, что в func.generate_operation_receipt (4081 + 8 цифр + **** + 4)."""
    try:
        import func as _func

        return _func.format_receipt_debit_account_line(account or "")
    except Exception:
        d = re.sub(r"\D", "", str(account or ""))
        if len(d) >= 4:
            eight = (d[4:12] + "00000000")[:8] if len(d) > 4 else "00000000"
            last4 = d[-4:] if len(d) >= 4 else "7576"
            return "4081" + eight + "****" + last4
        return "408100000000****7576"

def generate_kvit():
    return f"{random.randint(100,999)}-{random.randint(100,999)}-{random.randint(100,999)}"

def generate_sbp_operation_id():
    prefix = random.choice(["A", "B"])
    dt = datetime.now()
    base = datetime(2009, 7, 28)
    days = (dt.date() - base.date()).days
    code_day = f"{days:04d}"
    dt_utc = dt - timedelta(hours=3)
    time_str = f"{dt_utc.hour:02d}{dt_utc.minute:02d}{dt_utc.second:02d}"
    seq = random.randint(10000, 99999)
    extra = random.choice(["0G", "0J", "0B", "A0", "O0"])
    op_code = random.choice(["000006", "000013", "000008", "000014"])
    client_id = "0011700501"
    sbp_id = f"{prefix}{code_day}{time_str}{seq}{extra}{op_code}{client_id}"
    return sbp_id[:32]

def safe_json(text):
    try:
        return json.loads(text)
    except:
        return None


def _is_tbank_app_embedded_url(url: str) -> bool:
    return "t-bank-app" in (url or "").lower()


def _is_v1_pay_url(url: str) -> bool:
    """Веб: /api/common/v1/pay | Встраиваемый клиент: …/v1/pay на *.t-bank-app.ru."""
    u = (url or "").lower()
    if "/api/common/v1/pay" in u:
        return True
    return _is_tbank_app_embedded_url(u) and "/v1/pay" in u


def _is_v1_operations_feed_url(url: str) -> bool:
    """Лента операций (не гистограмма / не категории)."""
    u = (url or "").lower()
    if "/api/common/v1/operations" in u:
        return True
    if not _is_tbank_app_embedded_url(u):
        return False
    if "operations_histogram" in u or "operations_category" in u:
        return False
    return "/v1/operations" in u


def _grab_pay_like_dict(d: dict, amount: float, phone, name) -> tuple:
    """Извлечь сумму/контакты из одного dict (рекурсивно по payload)."""
    if not isinstance(d, dict):
        return amount, phone, name
    for key in ("moneyAmount", "amount", "totalAmount", "payAmount", "sum"):
        block = d.get(key)
        if isinstance(block, dict) and block.get("value") is not None:
            try:
                v = float(block["value"])
                if v > 0:
                    amount = max(amount, v)
            except (TypeError, ValueError):
                pass
        elif isinstance(block, (int, float)):
            try:
                v = float(block)
                if v > 0:
                    amount = max(amount, v)
            except (TypeError, ValueError):
                pass
    pf = d.get("providerFields") or d.get("recipient") or {}
    if isinstance(pf, dict):
        phone = pf.get("pointer") or pf.get("phone") or pf.get("msisdn") or phone
        name = pf.get("maskedFIO") or pf.get("name") or name
    phone = d.get("phone") or d.get("pointer") or phone
    name = d.get("maskedFIO") or d.get("recipientName") or name
    nested = d.get("payload")
    if isinstance(nested, dict):
        amount, phone, name = _grab_pay_like_dict(nested, amount, phone, name)
    return amount, phone, name


def _parse_v1_pay_request_body(body_text: str, content_type: str = "") -> tuple:
    """payParameters= (mybank), JSON или x-www-form-urlencoded (встроенный /v1/pay)."""
    amount, phone, name = 0.0, None, None
    raw = body_text or ""
    ct = (content_type or "").lower()

    if "payParameters=" in raw:
        try:
            param = raw.split("payParameters=")[1].split("&")[0]
            data = safe_json(unquote(param))
            if data:
                amount, phone, name = _grab_pay_like_dict(data, amount, phone, name)
        except Exception:
            pass
        return amount, phone, name

    t = raw.strip()
    if t.startswith("{"):
        try:
            data = json.loads(t)
            amount, phone, name = _grab_pay_like_dict(data, amount, phone, name)
        except Exception:
            pass
        return amount, phone, name

    # form-urlencoded без ведущей «{» — типично для iOS /v1/pay
    if t and ("=" in t) and ("&" in t or "=" in t):
        try:
            qs = urllib.parse.parse_qs(t, keep_blank_values=True)
            for key in ("payParameters", "payload", "pay_params", "parameters", "body"):
                vals = qs.get(key) or []
                for v in vals:
                    if not (v or "").strip():
                        continue
                    try:
                        data = json.loads(unquote(v))
                        amount, phone, name = _grab_pay_like_dict(data, amount, phone, name)
                    except Exception:
                        continue
            for flat in ("moneyAmount", "amount", "sum", "totalAmount"):
                vals = qs.get(flat) or []
                for v in vals:
                    try:
                        amount = max(amount, float(str(v).replace(",", ".").strip()))
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass

    return amount, phone, name


def _payment_commission_ok_dict() -> dict:
    """Тот же успешный ответ комиссии, что отдаём из request-хука."""
    return {
        "resultCode": "OK",
        "trackingId": "COMMISSION_OK",
        "payload": {
            "providerId": "p2p-anybank",
            "description": "Комиссия не взимается банком",
            "shortDescription": "Комиссия не взимается банком",
            "limit": 1000000.0,
            "value": {"value": 0, "currency": {"code": 643, "name": "RUB", "strCode": "643"}},
            "minAmount": 10.0,
            "maxAmount": 1000000.0,
            "total": {"value": 0.0, "currency": {"code": 643, "name": "RUB", "strCode": "643"}},
            "unfinishedFlag": False,
            "externalFees": [],
        },
    }


# Вспомогательные запросы сценария СБП/перевода; ошибка на любом из них даёт модалку «Не удалось загрузить».
_TRANSFER_AUX_URL_MARKERS = (
    "get_requisites",
    "payment_commission",
    "providers/find",
    "phonetransfer",
    "phone-transfer",
    "money-transfer",
    "internal-transfer",
    "outgoingtransfer",
    "incomingtransfer",
    "multi_transfer",
    "transfer_session",
    "money-session",
    "cash-flow",
    "cash_flow",
    "operation/info",
    "operation_detail",
    "operationby",
    "payment/status",
    "pay/result",
    "transfer/confirm",
)


def _url_matches_transfer_aux(url_lower: str) -> bool:
    return any(m in url_lower for m in _TRANSFER_AUX_URL_MARKERS)


def _neutralize_transfer_aux_http_errors(flow: http.HTTPFlow, url_lower: str) -> bool:
    """
    Если сервер вернул 4xx/5xx на вспомогательных API перевода, клиент часто
    показывает «Не удалось загрузить данные», хотя остальное (баланс, анкеты) уже есть.
    """
    sc = flow.response.status_code
    if sc < 400:
        return False
    if not _url_matches_transfer_aux(url_lower):
        return False
    flow.response.status_code = 200
    flow.response.headers["Content-Type"] = "application/json; charset=utf-8"
    if "payment_commission" in url_lower:
        flow.response.text = json.dumps(_payment_commission_ok_dict(), ensure_ascii=False)
    else:
        flow.response.text = '{"resultCode":"OK","payload":null}'
    print(f"[transfer] подменён HTTP {sc} для вспомогательного API перевода ({url_lower[:96]})")
    return True


def _handle_payment_commission_request(flow: http.HTTPFlow) -> None:
    """Как transfer2: комиссия 0 и предаём merchant/банк в transfer_data до pay."""
    global _fake_payment_done
    try:
        body = flow.request.get_text() or ""
        pay_data = None
        if "payParameters=" in body:
            params = urllib.parse.parse_qs(body)
            pay_str = (params.get("payParameters") or [""])[0]
            pay_data = json.loads(urllib.parse.unquote(pay_str))
        elif body.strip().startswith("{"):
            pay_data = json.loads(body)
        elif "=" in body and "&" in body:
            qs = urllib.parse.parse_qs(body, keep_blank_values=True)
            pay_str = (
                (qs.get("payParameters") or qs.get("payload") or qs.get("parameters") or [""])[0]
            )
            if not pay_str:
                return
            pay_data = json.loads(urllib.parse.unquote(pay_str))
        else:
            return
        if not isinstance(pay_data, dict):
            return
        amount = float(pay_data.get("moneyAmount", 0) or pay_data.get("amount", 0) or 0)
        amt_grab, _, _ = _grab_pay_like_dict(pay_data, 0.0, None, None)
        if amt_grab > amount:
            amount = amt_grab
        if amount < 10 or amount > 1_000_000:
            amount = max(10, min(1_000_000, amount))
        provider = pay_data.get("providerFields") or {}
        merchant_name = provider.get("merchantName") or provider.get("shortName") or provider.get("name") or provider.get("title")
        merchant_logo = (
            provider.get("merchantLogo")
            or provider.get("logo")
            or provider.get("icon")
            or provider.get("imageUrl")
            or provider.get("logoUrl")
            or provider.get("brandLogo")
        )
        if merchant_name:
            transfer_data["is_merchant_payment"] = True
            transfer_data["merchant_name"] = merchant_name
            transfer_data["merchant_logo"] = merchant_logo
        else:
            transfer_data["is_merchant_payment"] = False
            raw_bank = provider.get("bank") or transfer_data.get("bank_receiver") or "Т-Банк"
            transfer_data["bank_receiver"] = raw_bank
            transfer_data["bank_logo"] = provider.get("bankLogo") or provider.get("logo") or get_bank_logo(raw_bank)
        transfer_data["amount"] = amount
        transfer_data["receiver_phone"] = format_phone(provider.get("pointer"))
        transfer_data["receiver_name"] = (provider.get("maskedFIO") or "Получатель").replace("+", " ").strip()
        transfer_data["sender_name"] = clean_sender_name(config.get("name", {}).get("full_name", "Клиент Т-Банка"))
        save_data(transfer_data)
        _fake_payment_done = False
        success = _payment_commission_ok_dict()
        flow.response = http.Response.make(
            200,
            json.dumps(success, ensure_ascii=False).encode("utf-8"),
            {"Content-Type": "application/json; charset=utf-8"},
        )
    except Exception:
        pass


def _add_to_fake_history() -> bool:
    """Добавляет полноформатную операцию в fake_history (transfer2) для ленты и PDF."""
    global _last_fake_ts, _last_fake_op_id, _last_fake_hash, _fake_payment_done
    if transfer_data.get("amount", 0) <= 0 or _fake_payment_done:
        return False
    current_time = time.time()
    if current_time - _last_fake_ts < 8:
        return False
    operation_id = "UNIFIED_" + str(int(current_time * 1000))
    if operation_id == _last_fake_op_id:
        return False
    current_hash = f"{transfer_data.get('amount')}_{transfer_data.get('receiver_phone')}_{transfer_data.get('receiver_name')}"
    if current_hash == _last_fake_hash:
        return False
    _last_fake_hash = current_hash
    _last_fake_op_id = operation_id
    _last_fake_ts = current_time
    transfer_data["transaction_id"] = operation_id
    op_ts_ms = int(current_time * 1000)
    transfer_data["date_full"] = datetime.fromtimestamp(op_ts_ms / 1000).strftime("%d.%m.%Y, %H:%M:%S")
    if not transfer_data.get("kvit_number"):
        transfer_data["kvit_number"] = generate_kvit()
    save_data(transfer_data)

    if transfer_data.get("is_merchant_payment") and transfer_data.get("merchant_name"):
        display_name = transfer_data["merchant_name"]
        logo_url = transfer_data.get("merchant_logo") or get_bank_logo(display_name)
    else:
        display_name = transfer_data.get("receiver_name") or transfer_data.get("bank_receiver", "Т-Банк")
        logo_url = transfer_data.get("bank_logo") or get_bank_logo(transfer_data.get("bank_receiver", "Т-Банк"))

    bank_line = (clean_bank_name(transfer_data.get("bank_receiver") or "") or "").strip() or display_name
    op_for_receipt = {
        "id": operation_id,
        "date": transfer_data.get("date_full") or datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "amount": float(transfer_data.get("amount") or 0),
        "type": "Debit",
        "bank": bank_line,
        "title": (transfer_data.get("receiver_name") or "").strip() or display_name,
        "phone": str(transfer_data.get("receiver_phone") or "").strip(),
        "requisite_phone": str(transfer_data.get("receiver_phone") or "").strip(),
        "sender_name": str(transfer_data.get("sender_name") or "").strip(),
        "requisite_sender_name": str(transfer_data.get("sender_name") or "").strip(),
    }
    pdf_path = None
    try:
        pdf_path = generate_receipt_for_manual_op(op_for_receipt)
    except Exception as ex:
        print(f"[transfer] generate_operation_receipt (как у ручных): {ex}")
    if not pdf_path:
        pdf_path = create_real_receipt(operation_id)

    new_fake = {
        "id": operation_id,
        "operationId": {"value": operation_id, "source": "PrimeAuth"},
        "isExternalCard": False,
        "account": "5860068322",
        "card": "383947501",
        "ucid": "1386102627",
        "cardNumber": "220070******6404",
        "authorizationId": operation_id,
        "operationTime": {"milliseconds": op_ts_ms},
        "debitingTime": {"milliseconds": op_ts_ms},
        "type": "Debit",
        "status": "OK",
        "amount": {"value": int(transfer_data.get("amount", 0)), "currency": {"code": 643, "name": "RUB", "strCode": "643"}},
        "accountAmount": {"value": int(transfer_data.get("amount", 0)), "currency": {"code": 643, "name": "RUB", "strCode": "643"}},
        "cashback": 0.0,
        "cashbackAmount": {"value": 0.0, "currency": {"code": 643, "name": "RUB", "strCode": "643"}},
        "idSourceType": "Prime",
        "mcc": 0,
        "mccString": "0000",
        "description": display_name,
        "category": {"id": "45", "name": "Другое"},
        "brand": {"id": "11250", "name": display_name, "logo": logo_url, "baseColor": "f12e16", "fileLink": logo_url},
        "spendingCategory": {"id": "24", "name": "Переводы", "icon": "transfers-c1", "baseColor": "4FC5DF"},
        "senderDetails": "",
        "subcategory": display_name,
        "loyaltyBonus": [],
        "loyaltyPayment": [],
        "loyaltyBonusSummary": {"amount": 0.0},
        "categoryInfo": {
            "bankCategory": {"id": "24", "language": "ru", "name": "Переводы", "baseColor": "4FC5DF", "fileLink": "https://brands-prod.cdn-tinkoff.ru/general_logo/transfers-c1.png"},
            "metacategory": {"id": "12", "language": "ru", "name": "Финансы", "baseColor": "14B8AF", "fileLink": "https://bms-logo-prod.t-static.ru/general_logo/finance-3-meta.png"},
            "criteria": {"bulkVariety": {"type": "Description", "value": display_name}},
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
        "additionalInfo": [{"fieldName": "Тип перевода", "fieldValue": "Система быстрых платежей"}],
        "isDispute": False,
        "operationTransferred": False,
        "isOffline": False,
        "icon": logo_url,
        "analyticsStatus": "NotSpecified",
        "isTemplatable": False,
        "trancheCreationAllowed": False,
        "merchantKey": f"FAKE_SBP_DEBIT_{operation_id}",
        "posId": "585",
        "typeSerno": 151,
        "tags": [],
        "isInner": False,
        "isAuto": False,
        "merges": [],
        "documents": ["Statement"],
        "pdf_path": pdf_path,
        "date_full": transfer_data.get("date_full") or "",
        "receiver_phone": str(transfer_data.get("receiver_phone") or "").strip(),
        "receiver_name": str(transfer_data.get("receiver_name") or "").strip(),
        "bank_receiver": str(transfer_data.get("bank_receiver") or "").strip(),
    }
    fake_history.insert(0, new_fake)
    transfer_data["fake_history"] = fake_history
    save_data(transfer_data)
    _fake_payment_done = True
    print(f"[transfer] fake_history: {display_name}, amount={transfer_data.get('amount')} RUB")
    return True


def extract_bank(flow):
    """Ищет банк по bankMemberId, bank, bankName везде + из cookies и query"""
    bank_id = None
    bank_name = None

    # 1. Response body (самый приоритетный)
    if flow.response and flow.response.text:
        data = safe_json(flow.response.text)
        if data:
            # payload может быть list или dict
            payload = data.get("payload")
            if isinstance(payload, list) and payload:
                payload = payload[0]
            if isinstance(payload, dict):
                provider = payload.get("providerFields", payload) or payload
                bank_id = provider.get("bankMemberId") or provider.get("memberId") or provider.get("bankId")
                bank_name = provider.get("bank") or provider.get("bankName") or provider.get("brandName")

    # 2. Request body (payParameters)
    body = flow.request.get_text() or ""
    if "payParameters=" in body:
        try:
            param = body.split("payParameters=")[1].split("&")[0]
            data = safe_json(unquote(param))
            if data:
                provider = data.get("providerFields", data) or data
                bank_id = provider.get("bankMemberId") or provider.get("memberId") or provider.get("bankId")
                bank_name = provider.get("bank") or provider.get("bankName")
        except:
            pass

    # 3. Query parameters
    for key, value in flow.request.query.items():
        if "bank" in key.lower() and value:
            if value in BANK_MAP:
                return BANK_MAP[value]
            if "alfa" in value.lower():
                return "Альфа-Банк"
            if "tinkoff" in value.lower() or "tbank" in value.lower():
                return "Т-Банк"

    # 4. Cookies
    for cookie in flow.request.cookies.values():
        if "bank" in cookie.lower():
            if "alfa" in cookie.lower():
                return "Альфа-Банк"
            if "tinkoff" in cookie.lower():
                return "Т-Банк"

    # 5. Маппинг по ID (самый точный)
    if bank_id and str(bank_id) in BANK_MAP:
        return BANK_MAP[str(bank_id)]

    # 6. Название напрямую
    if bank_name and bank_name.strip() and bank_name != "Т-Банк":
        return bank_name

    # Если ничего не нашли — сохраняем предыдущий
    return transfer_data.get("bank_receiver", "Т-Банк")

def log_bank_fio(flow):
    url = flow.request.pretty_url.lower()
    print("\n" + "#" * 80)
    print(f"ЛОГГЕР БАНК -> {url}")
    new_bank = extract_bank(flow)
    if new_bank != transfer_data.get("bank_receiver"):
        print(f"   БАНК ОБНОВЛЁН → {new_bank}")
        transfer_data["bank_receiver"] = new_bank
        save_data(transfer_data)
    print("#" * 80 + "\n")


def generate_receipt_for_manual_op(op_data):
    """
    Тот же PDF, что при добавлении операции вручную (func.generate_operation_receipt).
    op_data — dict из manual_operations или совместимый с полями fake_history/transfer_data.
    """
    import func

    amt = op_data.get("amount")
    if isinstance(amt, dict):
        amt = float(amt.get("value") or 0)
    else:
        amt = abs(float(amt or 0))
    if amt <= 0:
        da = op_data.get("debitAmount")
        if da is not None:
            amt = abs(float(da or 0))
    if amt <= 0:
        return None

    date_s = ""
    ot0 = op_data.get("operationTime")
    if isinstance(ot0, dict):
        try:
            _ms = int(ot0.get("milliseconds") or 0)
            if _ms > 0:
                date_s = datetime.fromtimestamp(_ms / 1000).strftime("%d.%m.%Y, %H:%M:%S")
        except (TypeError, ValueError, OSError):
            pass
    if not date_s:
        date_s = (
            (op_data.get("date") or op_data.get("date_full") or "").strip()
            or datetime.now().strftime("%d.%m.%Y, %H:%M:%S")
        )
    if date_s and "," not in date_s and re.search(r"\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2}", date_s):
        date_s = re.sub(r"(\d{2}\.\d{2}\.\d{4})\s+", r"\1, ", date_s, count=1)
    bnk = (op_data.get("bank") or op_data.get("bank_receiver") or "").strip()
    ttl = (
        (op_data.get("title") or op_data.get("description") or op_data.get("receiver_name") or "").strip()
        or "Получатель"
    )
    if bnk:
        bnk = clean_bank_name(bnk) or bnk
    else:
        bnk = ttl

    mapped = {
        "id": str(op_data.get("id") or "manual")[:80],
        "date": date_s,
        "amount": amt,
        "type": (op_data.get("type") or "Debit").strip(),
        "bank": bnk,
        "title": ttl,
        "phone": (
            op_data.get("phone")
            or op_data.get("requisite_phone")
            or op_data.get("receiver_phone")
            or ""
        ),
        "requisite_phone": (
            op_data.get("requisite_phone")
            or op_data.get("phone")
            or op_data.get("receiver_phone")
            or ""
        ),
        "receipt_phone": (op_data.get("receipt_phone") or "").strip(),
        "sender_name": (op_data.get("sender_name") or "").strip(),
        "requisite_sender_name": (
            op_data.get("requisite_sender_name") or op_data.get("sender_name") or ""
        ).strip(),
    }
    return func.generate_operation_receipt(mapped)


def create_real_receipt(operation_id=None):
    if transfer_data.get("amount", 0) <= 0:
        print("Сумма = 0 - чек не создается")
        return None

    print("\n" + "=" * 100)
    print(f"ГЕНЕРАЦИЯ ЧЕКА: {transfer_data['amount']} руб, банк: {transfer_data['bank_receiver']}")

    tid = operation_id or transfer_data.get("transaction_id") or generate_id(8)
    id_short = str(tid).replace("UNIFIED_", "")[:16]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_pdf = f"receipt_{id_short}_{timestamp}.pdf"
    final_pdf = f"final_{output_pdf}"

    doc = fitz.open("sbpfinaltbanksend.pdf")
    page = doc[0]
    page.insert_font("my_normal", "TinkoffSans-Regular.ttf")
    page.insert_font("my_bold", "TinkoffSans-Medium.ttf")

    POSITIONS = {
        "date": (20, 86.5),
        "big_summ": (110, 92, 234.6, 142),
        "summ": (110, 173.98, 242.05, 187.98),
        "sender": (110, 214, 250, 228.18),
        "number": (110, 239-5, 250.02, 253.08-5),
        "name": (110, 239+20-5, 250.02, 253.08+20-5),
        "bank": (110, 239+40-5, 250.02, 253.08+40-5),
        "invoice": (110, 239+60-5, 250.02, 253.08+60-5),
        "identificator": (124.98999786376953, 314.00299072265625 + 8.18),
        "identificator2": (226.91000366210938, 325.0829772949219 + 8.18),
        "kvit": (93.35900115966797, 450.9629821777344 + 8.18),
    }

    page.insert_text(POSITIONS["date"], transfer_data["date_full"], fontname="my_normal", fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], f"{int(transfer_data['amount']):,}".replace(",", " "), fontname="my_bold", fontsize=16, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], f"{int(transfer_data['amount']):,}".replace(",", " "), fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], transfer_data["sender_name"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], transfer_data["receiver_phone"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], transfer_data["receiver_name"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["bank"], clean_bank_name(transfer_data["bank_receiver"]), fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["invoice"], format_account(transfer_data["sender_account"]), fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)

    bukva = random.choice(["А", "В"])
    page.insert_text(POSITIONS["identificator"], bukva + f"{random.randint(111111111111, 999999999999)}" + "406000004001" + f"{random.randint(11,99)}", fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["identificator2"], f"{random.randint(11111, 99999)}", fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["kvit"], transfer_data["kvit_number"], fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))

    doc.save(output_pdf, clean=True, deflate=True, garbage=4)
    doc.close()

    try:
        subprocess.run([
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={final_pdf}", output_pdf
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"Сжато до ~60 КБ: {final_pdf}")
    except:
        final_pdf = output_pdf

    transfer_data["last_pdf_path"] = final_pdf
    save_data(transfer_data)

    try:
        if os.name == 'nt':
            os.startfile(final_pdf)
        else:
            cmd = 'open' if 'darwin' in os.uname().sysname.lower() else 'xdg-open'
            os.system(f'{cmd} "{final_pdf}"')
        print("Чек открыт автоматически")
    except:
        pass

    return final_pdf


def _parse_receipt_operation_id_from_flow(flow: http.HTTPFlow):
    operation_id = None
    if flow.request.query:
        q = dict(flow.request.query)
        operation_id = q.get("paymentId") or q.get("id") or q.get("operationId") or q.get("operation_id")
    if not operation_id:
        try:
            parsed = urllib.parse.urlparse(flow.request.pretty_url)
            qp = urllib.parse.parse_qs(parsed.query)
            operation_id = (
                (qp.get("paymentId") or qp.get("id") or qp.get("operationId") or qp.get("operation_id") or [None])[0]
            )
        except Exception:
            pass
    return operation_id


def _try_serve_receipt_pdf_response(flow: http.HTTPFlow, url_raw: str) -> bool:
    """
    Подмена ответа для URL с payment_receipt_pdf / operation_statement_pdf.
    Вызывается до проверок JSON/тела — как у кнопки «Квитанция» в мок‑переводе.
    Ручные операции и мок из истории: history.ensure_operation_receipt_pdf_path.
    """
    ul = url_raw.lower()
    if "payment_receipt_pdf" not in ul and "operation_statement_pdf" not in ul:
        return False
    if not is_bank_flow(flow):
        return False

    operation_id = _parse_receipt_operation_id_from_flow(flow)
    if not operation_id and fake_history:
        operation_id = fake_history[0].get("id")

    pdf_path = None
    if operation_id:
        try:
            pdf_path = history_mod.ensure_operation_receipt_pdf_path(str(operation_id))
        except Exception:
            pdf_path = None
    if pdf_path and Path(pdf_path).exists():
        pass
    else:
        pdf_path = None
        if operation_id and fake_history:
            for op in fake_history:
                if not isinstance(op, dict):
                    continue
                if op.get("id") == operation_id or op.get("transaction_id") == operation_id:
                    pdf_path = op.get("pdf_path")
                    break
        if not pdf_path and fake_history:
            pdf_path = fake_history[0].get("pdf_path") if isinstance(fake_history[0], dict) else None
        if not pdf_path:
            pdf_path = transfer_data.get("last_pdf_path")

    if not pdf_path or not Path(pdf_path).exists():
        fb = operation_id or ("FALLBACK_" + str(int(time.time() * 1000)))
        pdf_path = None
        if operation_id and fake_history:
            for hop in fake_history:
                if not isinstance(hop, dict):
                    continue
                if str(hop.get("id")) != str(operation_id):
                    continue
                amt = hop.get("amount")
                if isinstance(amt, dict):
                    amt = float(amt.get("value") or 0)
                else:
                    amt = float(amt or 0)
                od = {
                    "id": hop.get("id"),
                    "date": hop.get("date_full")
                    or transfer_data.get("date_full")
                    or datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
                    "amount": amt,
                    "type": "Debit",
                    "bank": (clean_bank_name(hop.get("bank_receiver") or "") or "").strip()
                    or (hop.get("description") or hop.get("subcategory") or "Перевод"),
                    "title": (hop.get("receiver_name") or hop.get("description") or "").strip()
                    or "Получатель",
                    "requisite_phone": str(hop.get("receiver_phone") or "").strip(),
                    "phone": str(hop.get("receiver_phone") or "").strip(),
                    "sender_name": str(transfer_data.get("sender_name") or "").strip(),
                }
                try:
                    pdf_path = generate_receipt_for_manual_op(od)
                    if pdf_path:
                        hop["pdf_path"] = pdf_path
                        save_data(transfer_data)
                except Exception:
                    pdf_path = None
                break
        if not pdf_path or not Path(pdf_path).exists():
            pdf_path = create_real_receipt(fb)

    if pdf_path and Path(pdf_path).exists():
        with open(pdf_path, "rb") as f:
            flow.response.content = f.read()
        flow.response.headers["Content-Type"] = "application/pdf"
        flow.response.headers["Content-Disposition"] = f'inline; filename=receipt_{operation_id or "file"}.pdf'
        flow.response.status_code = 200
        print(f"Чек отдан: {pdf_path}")
        return True
    return False


def response(flow: http.HTTPFlow) -> None:
    if not flow.response:
        return
    url_raw = flow.request.pretty_url
    if not is_bank_flow(flow):
        return
    ensure_response_decoded(flow)
    # 422 на вспомогательных API рвёт сценарии (перевод, кэш) — отдаём пустой OK.
    ul = url_raw.lower()
    if flow.response.status_code == 422:
        if "social-api.t-bank-app.ru" in ul or "/social/" in ul:
            flow.response.status_code = 200
            flow.response.text = '{"resultCode":"OK","payload":null}'
            return
        if "gtech-tax-deduction" in ul or "tax-deduction" in ul:
            flow.response.status_code = 200
            flow.response.text = '{"resultCode":"OK","payload":{}}'
            return
        if "payment_commission" in ul:
            flow.response.status_code = 200
            flow.response.text = json.dumps(_payment_commission_ok_dict(), ensure_ascii=False)
            return
        if _url_matches_transfer_aux(ul):
            flow.response.status_code = 200
            flow.response.text = '{"resultCode":"OK","payload":null}'
            return
    if _neutralize_transfer_aux_http_errors(flow, ul):
        return
    if _try_serve_receipt_pdf_response(flow, url_raw):
        return
    if not flow.response.text:
        return
    url = url_raw.lower()
    if not is_jsonish_response(flow):
        return

    ct = (flow.response.headers.get("content-type") or "").lower()
    if _is_v1_operations_feed_url(url_raw) and "application/json" in ct:
        try:
            data = json.loads(flow.response.text)
            if isinstance(data, dict) and isinstance(data.get("payload"), list):
                existing_ids = {item.get("id") for item in data["payload"] if isinstance(item, dict) and item.get("id")}
                new_fakes = [op for op in fake_history if isinstance(op, dict) and op.get("id") not in existing_ids]
                if new_fakes:
                    data["payload"] = new_fakes + data["payload"]
                    flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception:
            pass

    if any(x in url for x in ["get_requisites", "payment_commission", "pay", "providers"]):
        log_bank_fio(flow)

    # Не использовать подстроку "event" — она входит в "events" (ленты встраиваемого банка).
    if any(x in url for x in ["web-gateway", "providers/find", "payment_commission", "get_requisites", "ping", "session_status", "bundles", "log/collect", "histogram"]) or "/gateway/v1/events" in url:
        return

    if _is_v1_pay_url(url_raw) and flow.request.method == "POST":
        transfer_data["date_full"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        tid = str(transfer_data.get("transaction_id") or "").strip()
        if not tid:
            tid = generate_id()
            transfer_data["transaction_id"] = tid
        transfer_data["payment_id"] = tid
        # Клиент дергает следующий экран по paymentId/operationId — без них нет анимации успеха.
        fake = {
            "resultCode": "OK",
            "trackingId": tid,
            "payload": {
                "status": "OK",
                "paymentId": tid,
                "operationId": tid,
                "paymentOperationId": tid,
            },
        }
        flow.response.text = json.dumps(fake, ensure_ascii=False)
        print(f"Экран успеха — Квитанция доступна (paymentId={tid})")
        return

def request(flow: http.HTTPFlow) -> None:
    global _fake_payment_done
    url = flow.request.pretty_url.lower()
    if any(x in url for x in ["/pay", "get_requisites", "payment_commission", "providers"]):
        log_bank_fio(flow)

    if "/payment_commission" in url and flow.request.method == "POST":
        _handle_payment_commission_request(flow)
        if flow.response is not None:
            return

    if not _is_v1_pay_url(flow.request.pretty_url or "") or flow.request.method != "POST":
        return

    body_text = flow.request.get_text() or ""
    ctype = flow.request.headers.get("Content-Type") or ""
    amount, phone, name = _parse_v1_pay_request_body(body_text, ctype)
    if amount <= 0:
        try:
            amount = float(transfer_data.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0
    if not phone:
        phone = transfer_data.get("receiver_phone")
    if not name:
        name = transfer_data.get("receiver_name")

    if amount > 0:
        _fake_payment_done = False
        transfer_data.update({
            "amount": amount,
            "receiver_phone": format_phone(phone),
            "receiver_name": (name or "Получатель").replace("+", " ").strip(),
            "sender_account": config.get("reki", {}).get("account", "408178100001****7576"),
            "sender_name": clean_sender_name(config.get("name", {}).get("full_name", "Клиент Т-Банка")),
            "bank_receiver": transfer_data["bank_receiver"],
            "transaction_id": generate_id(),
            "kvit_number": generate_kvit(),
            "sbp_operation_id": generate_sbp_operation_id(),
            "date_full": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        })
        save_data(transfer_data)
        print(f"Захвачено: {amount} руб, банк: {transfer_data['bank_receiver']}")
        added_fake = _add_to_fake_history()
        try:
            tr = ctrl.config.setdefault("transfers", {})
            if not added_fake:
                tr["total_out_rub"] = round(float(tr.get("total_out_rub", 0)) + amount, 2)
            bal = ctrl.config.setdefault("balance", {})
            cur = float(bal.get("new_balance", 0))
            bal["new_balance"] = max(0.0, round(cur - amount, 2))
            ctrl.save_config()
            if added_fake:
                print(f"[transfer] конфиг: баланс −{amount} ₽; расход в fake_history (без дубля в total_out_rub)")
            else:
                print(f"[transfer] конфиг: баланс −{amount} ₽; total_out_rub={tr['total_out_rub']} ₽")
            history_mod.sync_panel_income_expense_with_operations()
        except Exception as ex:
            print(f"[transfer] не удалось обновить balance/transfers в config: {ex}")

print("[TRANSFER v37] fake_history + commission + operations inject; банк по bankMemberId")