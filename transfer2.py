"""
tbank_unified_transfer.py
ПОЛНЫЙ КОД СО ВСЕМ — PDF ОТДАЁТСЯ МГНОВЕННО ИЗ ИСТОРИИ И ПОСЛЕ ПЕРЕВОДА
100% защита от ошибки «не удалось загрузить чек»
"""

import json
from mitmproxy import http
import time
import urllib.parse
import random
from datetime import datetime
import os
import fitz
from pathlib import Path
import subprocess

CONFIG_FILE = "config.json"
DATA_FILE = "last_transfer.json"

# ================== ПОЛНЫЙ СПИСОК ВСЕХ БАНКОВ ==================
BANK_MAP = {
    "1": "Сбербанк", "2": "Альфа-Банк", "3": "ВТБ", "4": "Райффайзенбанк", "5": "Открытие",
    "6": "РНКБ Банк", "7": "Газпромбанк", "8": "Золотая Корона", "9": "Почта Банк",
    "10": "Совкомбанк", "11": "Промсвязьбанк", "12": "Банк Санкт-Петербург", "13": "Хоум Банк",
    "14": "МТС Банк", "15": "Ак Барс", "16": "МКБ", "17": "Россельхозбанк", "18": "ЮниКредит Банк",
    "19": "ВБРР", "20": "АБ Россия", "21": "Банк Дом.РФ", "22": "НОВИКОМБАНК", "23": "Уралсиб",
    "24": "Русский Стандарт", "25": "УБРиР", "26": "Абсолют Банк", "27": "ЮMoney", "28": "Банк Зенит",
    "29": "Транскапиталбанк", "30": "Цифра Банк", "31": "Драйв Клик Банк", "32": "Локо-Банк",
    "33": "Ренессанс Кредит", "34": "ВУЗ-Банк", "35": "ОТП Банк", "36": "Банк Авангард",
    "37": "Металлинвестбанк", "38": "Азиатско-Тихоокеанский Банк", "39": "Сургутнефтегазбанк",
    "40": "Экспобанк", "41": "Кредит Европа Банк", "42": "Таврический Банк", "43": "Банк Аверс",
    "44": "Кубань Кредит", "45": "Центр-Инвест", "46": "Банк Финсервис", "47": "ББР Банк",
    "48": "Банк Центрокредит", "49": "Банк Синара", "50": "Ингосстрах Банк", "51": "SBI Bank",
    "52": "Примсоцбанк", "53": "Газэнергобанк", "54": "Меткомбанк", "55": "Банк Интеза",
    "56": "БКС Банк", "57": "СДМ-Банк", "58": "Тойота Банк", "59": "Банк Левобережный",
    "60": "Севергазбанк", "61": "Солидарность", "62": "Банк Объединенный Капитал", "63": "Челябинвестбанк",
    "64": "Международный Финансовый Клуб", "65": "Фора Банк", "66": "Челиндбанк", "67": "Генбанк",
    "68": "Банк Держава", "69": "Энерготрансбанк", "70": "Дальневосточный Банк", "71": "Кредит Урал Банк",
    "72": "Аресбанк", "73": "Банк Приморье", "74": "Тимер Банк", "75": "Быстробанк", "76": "ПСКБ",
    "77": "MC Bank Rus", "78": "Интерпрогрессбанк", "79": "НС Банк", "80": "Банк Национальный Стандарт",
    "81": "Ланта Банк", "82": "Алмазэргиэнбанк", "83": "Банк Хлынов", "84": "Росдорбанк",
    "85": "Модульбанк", "86": "НБД-Банк", "87": "Акибанк", "88": "Урал ФД", "89": "Инбанк",
    "90": "Экономбанк", "91": "Москоммерцбанк", "92": "Татсоцбанк", "93": "Акцепт", "94": "НК Банк",
    "95": "Энергобанк", "96": "Норвик Банк", "97": "Агропромкредит", "98": "РЕСО Кредит",
    "99": "Реалист Банк", "100": "Морской Банк", "101": "Банк Александровский", "102": "Прио Внешторгбанк",
    "103": "Тольяттихимбанк", "104": "Кошелев-Банк", "105": "Пойдём!", "106": "Ишбанк",
    "107": "Банк Оренбург", "108": "Еврофинанс Моснарбанк", "109": "Банк ПТБ", "110": "Алеф-Банк",
    "111": "Развитие-Столица", "112": "Форштадт", "113": "Автоторгбанк", "114": "Банк Раунд",
    "115": "Руснарбанк", "116": "Нико-Банк", "117": "Датабанк", "118": "БЖФ Банк", "119": "Нацинвестпромбанк",
    "120": "Банк Казани", "121": "ЮГ-Инвестбанк", "122": "Пробанк", "123": "Русский Универсальный Банк",
    "124": "Банк Снежинский", "125": "Финам Банк", "126": "Екатеринбург Банк", "127": "Мир Бизнес Банк",
    "128": "Банк МБА-Москва", "129": "Итуруп Банк", "130": "Банк Мир Привилегий", "131": "Банк Ростфинанс",
    "132": "Национальный Резервный Банк", "133": "Углеметбанк", "134": "Новобанк", "135": "Солид Банк",
    "136": "Томскпромстройбанк", "137": "Хакасский Муниципальный Банк", "138": "Трансстройбанк",
    "139": "Роял Кредит Банк", "140": "Сибсоцбанк", "141": "Кузнецкбизнесбанк", "142": "Банк Агророс",
    "143": "Белгородсоцбанк", "144": "Гута Банк", "145": "Славия Банк", "146": "Северный Народный Банк",
    "147": "Газтрансбанк", "148": "Земский Банк", "149": "Банк Новый Век", "150": "Венецбанк",
    "151": "Петербургский Городской Банк", "152": "Енисейский объединенный банк", "153": "Индустриальный Сберегательный Банк",
    "154": "Стройлесбанк", "155": "Крокус Банк", "156": "Банк Вологжанин", "157": "Авто Финанс Банк",
    "158": "Бланк банк", "159": "КБ Долинск", "160": "Нокссбанк", "161": "Владбизнесбанк", "162": "Братский Народный Банк",
    "163": "Кубаньторгбанк", "164": "Банк Кремлевский", "165": "Банк Заречье", "166": "Банк \"Элита\"",
    "167": "Московский Коммерческий Банк", "168": "Первый Дортрансбанк", "169": "Уралфинанс", "170": "Синко-Банк",
    "171": "Уралпромбанк", "172": "Тендер-Банк", "173": "Банк Москва-Сити", "174": "Юнистрим", "175": "Первый Инвестиционный Банк",
    "176": "Социум Банк", "177": "Внешфинбанк", "178": "Банк Йошкар-Ола", "179": "ИК Банк", "180": "Банк Саратов",
    "181": "VK Pay", "182": "Яндекс Банк", "183": "МОБИ.Деньги", "184": "Вайлдберриз Банк", "185": "НКО Монета",
    "186": "Озон Банк", "187": "Элплат", "188": "Мобильная карта", "189": "Хайс", "190": "Точка Банк",
    "191": "Банк Живаго", "192": "Яринтербанк", "193": "СтавропольПромСтройБанк", "194": "МТС Деньги", "195": "Первоуральскбанк",
    "196": "Авито Кошелек", "197": "ФИНСТАР БАНК", "198": "Рокетбанк", "199": "Банк Оранжевый", "200": "Банк Кузнецкий",
    "201": "ЦМРБанк", "202": "Свой Банк", "203": "Банк РСИ", "205": "Банк ЧБРР"
}

# ================== ПОЛНЫЙ СПИСОК ЛОГО ==================
BANK_LOGO = {
    "Сбербанк": "https://brands-prod.cdn-tinkoff.ru/general_logo/sber.png",
    "Т-Банк": "https://brands-prod.cdn-tinkoff.ru/general_logo/tinkoff-new.png",
    "Альфа-Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWxmYWJhbmsucG5n",
    "ВТБ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdnRiYmFuay5wbmc=",
    "ПСБ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJvbXN2eWF6YmFuay5wbmc=",
    "Яндекс Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veWEtYmFuay5wbmc=",
    "WB Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "Wildberries (Вайлдберриз Банк)": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "Вайлдберриз Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc=",
    "Совкомбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc292Y29tYmFuay5wbmc=",
    "Ак Барс Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiLnBuZw==",
    "МТС Деньги": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzZGVuZ2kucG5n",
    "Уралсиб": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdXJhbHNpYi5wbmc=",
    "Фора банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZm9yYWJhbmsucG5n",
    "АБ Россия": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiX3Jvc3NpeWEucG5n",
    "РНКБ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm5jYi5wbmc=",
    "Банк Акцепт": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtjZXB0LnBuZw==",
    "Дом РФ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZG9tcmZiYW5rLnBuZw==",
    "УБРИР": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdWJyaXIucG5n",
    "Кредит Европа Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28va3JlZGl0LWV2cm9wYS1iYW5rLnBuZw==",
    "Почта банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcG9jaHRhLWJhbmsucG5n",
    "Цифра Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2lmcmEtYmFuay5wbmc=",
    "МКБ": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWtiLW5ldy0yLnBuZw==",
    "Россельхозбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm9zc2VsaG96YmFuay5wbmc=",
    "Банк Приморье": "https://bms-logo-prod.t-static.ru/general_logo/primbank-new.png",
    "Рокет Банк": "https://bms-logo-prod.t-static.ru/general_logo/rocketbank.png",
    "Райффайзенбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcmFpZmZlaXNlbi5wbmc=",
    "Озон Банк (Ozon)": "https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png",
    "Озон Банк": "https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png",
    "Цупис": "https://bms-logo-prod.t-static.ru/general_logo/1cupis-mplat.png",
    "СКБ Примсоцбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJpbS1zb2MtYmFuay5wbmc=",
    "Банк Санкт-Петербург": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYmFua3NwYi5wbmc=",
    "МББ банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWJicnUucG5n",
    "Центр Инвест Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2VudHJpbnZlc3QucG5n",
    "Окто Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb2N0by1iYW5rLnBuZw==",
    "ЮMoney": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veW9vbW9uZXkucG5n",
    "ОТП-Банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb3RwYmFuay1uZXcucG5n",
    "Газпромбанк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2F6cHJvbWJhbmsucG5n",
    "МТС-банк": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzLWJhbmsucG5n",
    "Таджикский сомони": "https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZXNraGF0YS1uZXcucG5n",
}

def get_bank_logo(bank_name):
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
    if any(x in name for x in ["Wildberries", "Вайлдберриз", "WB"]):
        return "Wildberries (Вайлдберриз Банк)"
    return name

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"name": {"first_name": "Алексей", "last_name": "Попов"}, "reki": {"account": "408178102969327857"}}

def get_short_sender():
    cfg = load_config()
    name = cfg.get("name", {})
    first = name.get("first_name", "Алексей")
    last = name.get("last_name", "Попов")
    return f"{first} {last[0].upper()}." if first and last else "Клиент Т-Банка"

def get_sender_account():
    cfg = load_config()
    return cfg.get("reki", {}).get("account", "408178102969327857")

def get_kvit_number():
    return f"{random.randint(100,999)}-{random.randint(100,999)}"

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

data = load_data()
fake_history = data.get("fake_history", [])
transfer_data = data or {
    "amount": 0.0,
    "transaction_id": None,
    "date_full": None,
    "receiver_phone": None,
    "receiver_name": None,
    "sender_name": get_short_sender(),
    "sender_account": get_sender_account(),
    "bank_receiver": "Т-Банк",
    "bank_logo": None,
    "merchant_name": None,
    "merchant_logo": None,
    "is_merchant_payment": False,
    "kvit_number": None,
    "last_pdf_path": None,
    "fake_history": fake_history
}

class TBankUnifiedVisualTransfer:
    def __init__(self):
        self.last_added_timestamp = 0
        self.last_operation_id = None
        self.last_transfer_hash = ""
        self.payment_processed = False

    def request(self, flow: http.HTTPFlow) -> None:
        if not flow.request:
            return
        host = flow.request.host.lower()
        path = flow.request.path.lower()
        if not any(d in host for d in ["tbank.ru", "tinkoff.ru", "t-bank-app.ru", "api.tbank", "www.tbank.ru"]):
            return

        if "/payment_commission" in path and flow.request.method == "POST":
            try:
                body = flow.request.get_text()
                if body and "payParameters=" in body:
                    params = urllib.parse.parse_qs(body)
                    pay_str = params.get("payParameters", [""])[0]
                    pay_data = json.loads(urllib.parse.unquote(pay_str))
                    amount = float(pay_data.get("moneyAmount", 0))
                    if amount < 10 or amount > 1000000:
                        amount = max(10, min(1000000, amount))
                    provider = pay_data.get("providerFields", {})
                    merchant_name = provider.get("merchantName") or provider.get("shortName") or provider.get("name") or provider.get("title")
                    merchant_logo = provider.get("merchantLogo") or provider.get("logo") or provider.get("icon") or provider.get("imageUrl") or provider.get("logoUrl") or provider.get("brandLogo")
                    if merchant_name:
                        transfer_data["is_merchant_payment"] = True
                        transfer_data["merchant_name"] = merchant_name
                        transfer_data["merchant_logo"] = merchant_logo
                    else:
                        transfer_data["is_merchant_payment"] = False
                        raw_bank = provider.get("bank", "Т-Банк")
                        transfer_data["bank_receiver"] = raw_bank
                        transfer_data["bank_logo"] = provider.get("bankLogo") or provider.get("logo") or get_bank_logo(raw_bank)
                    transfer_data["amount"] = amount
                    transfer_data["receiver_phone"] = self.format_phone(provider.get("pointer"))
                    transfer_data["receiver_name"] = provider.get("maskedFIO", "Получатель")
                    transfer_data["sender_name"] = get_short_sender()
                    save_data(transfer_data)
                    self.payment_processed = False
                    self.last_added_timestamp = 0
                    success = {
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
                            "externalFees": []
                        }
                    }
                    flow.response = http.Response.make(200, json.dumps(success, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json; charset=utf-8"})
                    return
            except:
                pass

        if any(k in path for k in ["/api/common/v1/pay", "/pay", "/payment/execute", "/v1/pay", "/transfer/confirm", "/confirm", "/execute"]) and transfer_data.get("amount", 0) >= 10:
            if self.payment_processed:
                return
            fake_payment_id = "UNIFIED_" + str(int(time.time() * 1000))
            transfer_data["transaction_id"] = fake_payment_id
            transfer_data["date_full"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            transfer_data["sender_account"] = get_sender_account()
            save_data(transfer_data)
            self.add_to_fake_history()
            self.subtract_balance(transfer_data.get("amount", 0))
            fake_success = {"resultCode": "OK", "trackingId": "UNIFIED_" + str(int(time.time())), "payload": {"paymentId": fake_payment_id, "status": "OK", "operationId": fake_payment_id}}
            flow.response = http.Response.make(200, json.dumps(fake_success, ensure_ascii=False).encode("utf-8"), {"Content-Type": "application/json; charset=utf-8"})
            return

    def response(self, flow: http.HTTPFlow) -> None:
        if not flow.response:
            return
        url = flow.request.pretty_url.lower()

        # ================== ИНЖЕКТ ИСТОРИИ ==================
        if "/api/common/v1/operations" in url and "application/json" in flow.response.headers.get("content-type", ""):
            try:
                text = flow.response.get_text()
                data = json.loads(text)
                if "payload" in data and isinstance(data["payload"], list):
                    existing_ids = {item.get("id") for item in data["payload"] if isinstance(item, dict) and item.get("id")}
                    new_fakes = [op for op in fake_history if op.get("id") not in existing_ids]
                    if new_fakes:
                        data["payload"] = new_fakes + data["payload"]
                    flow.response.set_text(json.dumps(data, ensure_ascii=False))
            except:
                pass

        # ================== 100% НАДЁЖНАЯ ВЫДАЧА PDF (2 МОМЕНТА) ==================
        if "payment_receipt_pdf" in url or "operation_statement_pdf" in url:
            print(f"[RECEIPT] Запрос чека: {flow.request.pretty_url}")

            # === МОМЕНТ 1: Кнопка «Квитанция» + МОМЕНТ 2: Сразу после перевода ===
            operation_id = None
            # 1. Из query-параметров
            if flow.request.query:
                q = dict(flow.request.query)
                operation_id = q.get("paymentId") or q.get("id") or q.get("operationId") or q.get("operation_id")
            # 2. Парсим URL вручную (на случай сложных ссылок)
            if not operation_id:
                try:
                    parsed = urllib.parse.urlparse(flow.request.pretty_url)
                    query_params = urllib.parse.parse_qs(parsed.query)
                    operation_id = (query_params.get("paymentId") or query_params.get("id") or 
                                   query_params.get("operationId") or query_params.get("operation_id") or [None])[0]
                except:
                    pass
            # 3. Если ID не нашли — берём самый свежий из истории
            if not operation_id and fake_history:
                operation_id = fake_history[0].get("id")

            pdf_path = None
            # Ищем PDF в истории
            if operation_id and fake_history:
                for op in fake_history:
                    if (op.get("id") == operation_id or 
                        op.get("transaction_id") == operation_id or 
                        op.get("paymentId") == operation_id or 
                        op.get("operationId") == operation_id):
                        pdf_path = op.get("pdf_path")
                        break

            # Fallback на последний сохранённый
            if not pdf_path and fake_history:
                pdf_path = fake_history[0].get("pdf_path") or transfer_data.get("last_pdf_path")

            # Если PDF нет или файл удалён — генерируем новый
            if not pdf_path or not Path(pdf_path).exists():
                print("[RECEIPT] PDF не найден — генерирую новый")
                pdf_path = create_real_receipt("FALLBACK_" + str(int(time.time())))

            if pdf_path and Path(pdf_path).exists():
                try:
                    with open(pdf_path, "rb") as f:
                        pdf_content = f.read()

                    flow.response = http.Response.make(
                        200,
                        pdf_content,
                        {
                            "Content-Type": "application/pdf",
                            "Content-Disposition": f"inline; filename=Квитанция_{operation_id or 'receipt'}.pdf",
                            "Content-Length": str(len(pdf_content)),
                            "Cache-Control": "no-cache, no-store, must-revalidate",
                            "Pragma": "no-cache",
                            "Expires": "0"
                        }
                    )
                    print(f"[RECEIPT] PDF ОТДАН МГНОВЕННО! Размер: {len(pdf_content)} байт | ID: {operation_id}")
                    return
                except Exception as e:
                    print(f"[ERROR] Не удалось прочитать PDF: {e}")
            else:
                print("[RECEIPT] Критическая ошибка — PDF не найден")

    def subtract_balance(self, amount: float):
        if amount <= 0 or self.payment_processed:
            return
        try:
            cfg = load_config()
            old = int(cfg.get("balance", {}).get("new_balance", 99999))
            new_balance = max(0, old - int(amount))
            cfg.setdefault("balance", {})["new_balance"] = new_balance
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print(f"[BALANCE] {old} → {new_balance} ₽ (-{int(amount)} ₽)")
            self.payment_processed = True
        except Exception as e:
            print(f"[ERROR] Баланс: {e}")

    def add_to_fake_history(self):
        if transfer_data.get("amount", 0) <= 0 or self.payment_processed:
            return
        current_time = time.time()
        if current_time - self.last_added_timestamp < 8:
            return
        operation_id = "UNIFIED_" + str(int(current_time * 1000))
        if operation_id == self.last_operation_id:
            return
        current_hash = f"{transfer_data.get('amount')}_{transfer_data.get('receiver_phone')}_{transfer_data.get('receiver_name')}"
        if current_hash == self.last_transfer_hash:
            return
        self.last_transfer_hash = current_hash
        self.last_operation_id = operation_id
        self.last_added_timestamp = current_time
        transfer_data["transaction_id"] = operation_id
        transfer_data["date_full"] = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        save_data(transfer_data)

        pdf_path = create_real_receipt(operation_id)

        if transfer_data.get("is_merchant_payment") and transfer_data.get("merchant_name"):
            display_name = transfer_data["merchant_name"]
            logo_url = transfer_data.get("merchant_logo") or get_bank_logo(display_name)
        else:
            display_name = transfer_data.get("receiver_name") or transfer_data.get("bank_receiver", "Т-Банк")
            logo_url = transfer_data.get("bank_logo") or get_bank_logo(transfer_data.get("bank_receiver", "Т-Банк"))

        new_fake = {
            "id": operation_id,
            "operationId": {"value": operation_id, "source": "PrimeAuth"},
            "isExternalCard": False,
            "account": "5860068322",
            "card": "383947501",
            "ucid": "1386102627",
            "cardNumber": "220070******6404",
            "authorizationId": operation_id,
            "operationTime": {"milliseconds": int(time.time() * 1000) + 2000},
            "debitingTime": {"milliseconds": int(time.time() * 1000) + 2000},
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
                "criteria": {"bulkVariety": {"type": "Description", "value": display_name}}
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
            "pdf_path": pdf_path
        }
        fake_history.insert(0, new_fake)
        transfer_data["fake_history"] = fake_history
        save_data(transfer_data)
        print(f"[History] ✅ Добавлена операция → {display_name} | {transfer_data.get('amount')} ₽")

    def create_real_receipt(self, operation_id):
        return create_real_receipt(operation_id)

    def format_phone(self, phone):
        return format_phone(phone)

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
    if not account or len(account) < 16:
        return "40817810****7857"
    return account[:12] + "****" + account[-4:]

def create_real_receipt(operation_id):
    if transfer_data.get("amount", 0) <= 0:
        return None
    if not transfer_data.get("kvit_number"):
        transfer_data["kvit_number"] = get_kvit_number()
        save_data(transfer_data)

    print("\n" + "=" * 100)
    print(f"ГЕНЕРАЦИЯ ЧЕКА ДЛЯ ОПЕРАЦИИ {operation_id} | {transfer_data['amount']} руб | Банк: {transfer_data['bank_receiver']}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_pdf = f"receipt_{operation_id}_{timestamp}.pdf"
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

    bank_to_print = clean_bank_name(transfer_data["bank_receiver"])
    page.insert_text(POSITIONS["date"], transfer_data["date_full"], fontname="my_normal", fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], f"{int(transfer_data['amount']):,}".replace(",", " "), fontname="my_bold", fontsize=16, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], f"{int(transfer_data['amount']):,}".replace(",", " "), fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], transfer_data["sender_name"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], transfer_data["receiver_phone"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], transfer_data["receiver_name"], fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["bank"], bank_to_print, fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["invoice"], format_account(transfer_data["sender_account"]), fontname="my_normal", fontsize=9, color=(51/255, 51/255, 51/255), align=fitz.TEXT_ALIGN_RIGHT)

    bukva = random.choice(["А", "В"])
    page.insert_text(POSITIONS["identificator"], bukva + f"{random.randint(111111111111, 999999999999)}" + "406000004001" + f"{random.randint(11,99)}", fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["identificator2"], f"{random.randint(11111, 99999)}", fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["kvit"], transfer_data["kvit_number"], fontname="my_normal", fontsize=9.008945, color=(51/255, 51/255, 51/255))

    doc.save(output_pdf, clean=True, deflate=True, garbage=4)
    doc.close()

    try:
        subprocess.run(["gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4", "-dPDFSETTINGS=/ebook", "-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={final_pdf}", output_pdf], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except:
        final_pdf = output_pdf

    transfer_data["last_pdf_path"] = final_pdf
    save_data(transfer_data)

    try:
        if os.name == 'nt':
            os.startfile(final_pdf)
        else:
            os.system(f'xdg-open "{final_pdf}"' if 'linux' in os.uname().sysname.lower() else f'open "{final_pdf}"')
    except:
        pass

    return final_pdf


addons = [TBankUnifiedVisualTransfer()]

print("🚀 T-BANK UNIFIED TRANSFER ЗАПУЩЕН (улучшенная версия)")
print(" • PDF отдаётся мгновенно из истории И сразу после перевода")
print(" • 100% защита от ошибки «не удалось загрузить чек»")
print(" • Работает для 2 моментов: кнопка «Квитанция» + после перевода")