import fitz
from pymupdf import TEXT_ALIGN_RIGHT
import subprocess
import os
import re
import string
from pathlib import Path
import random
from datetime import datetime, timedelta


random_operations = ["Оплата в OZON", "Оплата в Rostics Moskva\nRUS", "Оплата в Wildberries",
                     "Оплата в MOSKVA-31-1-\nDODOPIZZA MOSCOW RUS", "Оплата в PYATEROCHKA\n12858 MOSCOW RUS",
                     "Оплата в Додо Пицца,\nМосква 117-2", "Оплата в YandexGo Moskva\nRUS", "Оплата в AV .AZBUKAVKUSA\nMOSCOW RUS", "Оплата в QSR 24219\nMoskva RUS", "Оплата в Russian Railways\nMoscow RUS",]

import subprocess
from pathlib import Path
import sys

def compress_pdf_ghostscript(
    input_path: str,
    output_path: str,
    quality: str = 'ebook',
    res: int = 220
) -> bool:
    if not Path(input_path).exists():
        raise FileNotFoundError(f"Input file {input_path} not found")

    gs_paths = [
        "/usr/bin/gs",
        "gs",
        "gswin64c.exe",
        "C:\\Program Files\\gs\\gs10.00.0\\bin\\bin\\gswin64c.exe",
        "C:\\Program Files (x86)\\gs\\gs10.00.0\\bin\\bin\\bgswin64c.exe"
    ]

    gs_bin = None
    for path in gs_paths:
        try:
            subprocess.run(
                [path, "--version"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            gs_bin = path
            break
        except (subprocess.SubprocessError, FileNotFoundError):
            continue

    if not gs_bin:
        raise FileNotFoundError(
            "Ghostscript not found. Install it: https://www.ghostscript.com/"
        )

    command = [
        gs_bin,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS=/{quality}",
        "-dSubsetFonts=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={res}",
        "-dGrayImageResolution=200",
        "-dMonoImageResolution=400",
        "-dDownsampleColorImages=true",
        "-dDownsampleGrayImages=true",
        "-dDownsampleMonoImages=true",
        "-dColorConversionStrategy=/LeaveColorUnchanged",
        "-dAutoFilterColorImages=false",
        "-dAutoFilterGrayImages=false",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={output_path}",
        input_path
    ]

    kwargs = {
        "check": True,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE
    }

    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        subprocess.run(command, **kwargs)
        return True
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        raise RuntimeError(f"Ghostscript error: {error_msg}")



def sber(
        data: str,
        card: str,
        name: str,
        sender_name: str,
        time: str,
        summ: str,
        summ_rub: str,
        output_pdf: str = "result.pdf"
):


    TEMPLATE_PATH = "tbanksend.pdf"
    FONT_NORMAL = "1.ttf"
    FONT_BOLD = "2.ttf"

    POSITIONS = {
        "date": (193, 90.48),
        "card": (222.75, 159.8),
        "name": (72, 171.33),
        "sender": (72, 232.32),
        "time": (72, 282.33),
        "summ": (72, 332.34),
        "summ_rub": (312, 332.34)
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"

    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")


    page.insert_text(POSITIONS["date"], data, fontname=font_b, fontsize=8, color=(0, 0, 0))
    page.insert_text(POSITIONS["card"], card, fontname=font_n, fontsize=10, color=(0, 0, 0))
    page.insert_text(POSITIONS["name"], name, fontname=font_n, fontsize=10, color=(0, 0, 0))
    page.insert_text(POSITIONS["sender"], sender_name, fontname=font_b, fontsize=10, color=(0, 0, 0))
    page.insert_text(POSITIONS["time"], f"{data} в {time}", fontname=font_b, fontsize=10, color=(0, 0, 0))
    page.insert_text(POSITIONS["summ"], f"{summ} ₽", fontname=font_b, fontsize=10, color=(0, 0, 0))
    page.insert_text(POSITIONS["summ_rub"], f"{summ} ₽", fontname=font_b, fontsize=10, color=(0, 0, 0))
    doc.save(
        output_pdf,
        clean=True,
        deflate=True,
        garbage=4,
        pretty=False
    )
    doc.close()
    


async def t2t_tbank(data, summ, sender_name, card, name, id):
    output_pdf = f"preresult_{id}.pdf"
    """Заполняет шаблон Tinkoff PDF"""
    TEMPLATE_PATH = "finaltbanksend.pdf"
    FONT_NORMAL = "TinkoffSans-Regular.ttf"
    FONT_BOLD = "TinkoffSans-Medium.ttf"

    POSITIONS = {
        "date": (20, 86.5),
        "summ": (0, 173.98, 242.05, 187.98),
        "big_summ": (0, 92, 234.6, 142),
        "sender": (0, 194, 250, 219),
        "card": (0, 214, 250, 228.18),
        "name": (0, 234, 250.02, 248.08),
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"
    card = '*'+card
    print(card)
    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")
    page.insert_text(POSITIONS["date"], data, fontname=font_n, fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], summ, fontname=font_b, fontsize=16, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], summ, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["card"], card, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255),align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], sender_name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    doc.save(output_pdf, clean=True, deflate=True, garbage=4, pretty=False)
    doc.close()
    compress_pdf_ghostscript(f"preresult_{id}.pdf", f"result_{id}.pdf")

async def t2t_number(data, summ, sender_name, number, name, id):
    output_pdf = f"preresult_{id}.pdf"
    """Заполняет шаблон Tinkoff PDF"""
    TEMPLATE_PATH = "finalt2t_number.pdf"
    FONT_NORMAL = "TinkoffSans-Regular.ttf"
    FONT_BOLD = "TinkoffSans-Medium.ttf"

    POSITIONS = {
        "date": (20, 86.5),
        "summ": (0, 173.98, 242.05, 187.98),
        "big_summ": (0, 92, 234.6, 142),
        "sender": (0, 194+20, 250, 219+20),
        "number": (0, 214+20, 250, 228.18+20),
        "name": (0, 234+20, 250.02, 248.08+20),
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"
    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")
    page.insert_text(POSITIONS["date"], data, fontname=font_n, fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], summ, fontname=font_b, fontsize=16, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], summ, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], number, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255),align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], sender_name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    doc.save(output_pdf, clean=True, deflate=True, garbage=4, pretty=False)
    doc.close()
    compress_pdf_ghostscript(f"preresult_{id}.pdf", f"result_{id}.pdf")

async def tcard(data, summ, sender_name, number, id):
    output_pdf = f"preresult_{id}.pdf"
    """Заполняет шаблон Tinkoff PDF"""
    TEMPLATE_PATH = "ткард.pdf"
    FONT_NORMAL = "TinkoffSans-Regular.ttf"
    FONT_BOLD = "TinkoffSans-Medium.ttf"

    POSITIONS = {
        "date": (20, 86.5),
        "summ": (0, 173.98, 242.05, 187.98),
        "big_summ": (0, 92, 234.6, 142),
        "sender": (0, 194, 250, 219),
        "number": (0, 234-20, 250.02, 248.08-20),
        "kvit": (98.03900146484375, 343.9582214355469+7.18),
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"
    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")
    page.insert_text(POSITIONS["date"], data, fontname=font_n, fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], summ, fontname=font_b, fontsize=16, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], summ, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], number, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255),align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], sender_name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_text(POSITIONS["kvit"], f"{random.randint(111, 999)}" + "-" + f"{random.randint(111, 999)}" + "-" + f"{random.randint(111, 999)}", fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255))
    doc.save(output_pdf, clean=True, deflate=True, garbage=4, pretty=False)
    doc.close()
    compress_pdf_ghostscript(f"preresult_{id}.pdf", f"result_{id}.pdf")

async def tbanksbp(data, summ, sender_name, number, name, bank, id):
    output_pdf = f"preresult_{id}.pdf"
    """Заполняет шаблон Tinkoff PDF"""
    TEMPLATE_PATH = "sbpfinaltbanksend.pdf"
    FONT_NORMAL = "TinkoffSans-Regular.ttf"
    FONT_BOLD = "TinkoffSans-Medium.ttf"

    POSITIONS = {
        "date": (20, 86.5),
        "summ": (110, 173.98, 242.05, 187.98),
        "big_summ": (110, 92, 234.6, 142),
        "sender": (110, 214, 250, 228.18),
        "number": (110, 239-5, 250.02, 253.08-5),
        "name": (110, 239+20-5, 250.02, 253.08+20-5),
        "bank": (110, 239+40-5, 250.02, 253.08+40-5),
        "invoice": (110, 239+60-5, 250.02, 253.08+60-5),
        "identificator": (124.98999786376953, 314.00299072265625+8.18),
        "identificator2": (226.91000366210938, 325.0829772949219 + 8.18),
        "kvit": (93.35900115966797, 450.9629821777344 + 8.18),
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"

    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")

    page.insert_text(POSITIONS["date"], data, fontname=font_n, fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], summ, fontname=font_b, fontsize=16, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], summ, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], sender_name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], number, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)

    page.insert_textbox(POSITIONS["bank"], bank, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["invoice"], f"40817810" + f"{random.randint(0, 9)}" + "000" + "****"+f"{random.randint(1111, 9999)}", fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    bukva = random.choice(["А", "В"])
    page.insert_text(POSITIONS["identificator"], bukva + f"{random.randint(111111111111, 999999999999)}" + "406000004001"+f"{random.randint(11, 99)}", fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["identificator2"], f"{random.randint(11111, 99999)}", fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["kvit"], f"{random.randint(111, 999)}" + "-" + f"{random.randint(111, 999)}" + "-" + f"{random.randint(111, 999)}", fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))

    doc.save(output_pdf, clean=True, deflate=True, garbage=4, pretty=False)
    doc.close()
    compress_pdf_ghostscript(f"preresult_{id}.pdf", f"result_{id}.pdf")


def format_phone_for_receipt(phone: str) -> str:
    """Как в блоке реквизитов в приложении: +7 927 445-76-16 (пробелы, без скобок)."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    p = (phone or "").strip()
    return p if p else ""


def format_receipt_sender_short(sender_raw: str, name_cfg: dict) -> str:
    """Имя и первая буква фамилии: «Ирина К.». Три слова: фамилия имя отчество."""
    raw = (sender_raw or "").strip()
    parts = raw.split()
    if len(parts) == 2 and len(parts[1]) <= 2 and parts[1].endswith("."):
        return raw
    if len(parts) >= 3:
        last, first = parts[0], parts[1]
        if last and first:
            return f"{first} {last[0]}."
    if len(parts) == 2:
        last, first = parts[0], parts[1]
        return f"{first} {last[0]}."
    if len(parts) == 1 and parts[0]:
        return parts[0]
    cfg = name_cfg or {}
    fn = (cfg.get("first_name") or "").strip()
    ln = (cfg.get("last_name") or "").strip()
    if fn and ln:
        return f"{fn} {ln[0]}."
    full = (cfg.get("full_name") or "").strip()
    if full:
        return format_receipt_sender_short(full, {})
    return "Клиент"


def format_receipt_debit_account_line(account_raw: str) -> str:
    """Счёт списания: 4081 + ровно 8 цифр + **** + последние 4 (как на квитанции СБП)."""
    digits = re.sub(r"\D", "", account_raw or "")
    if digits.startswith("4081") and len(digits) >= 12:
        eight = digits[4:12]
    elif len(digits) >= 12:
        eight = digits[4:12]
    elif len(digits) > 4:
        eight = (digits[4:] + "00000000")[:8]
    else:
        eight = "".join(str(random.randint(0, 9)) for _ in range(8))
    prefix12 = "4081" + eight[:8]
    last4 = digits[-4:] if len(digits) >= 4 else f"{random.randint(1000, 9999):04d}"
    return prefix12 + "****" + last4


def generate_operation_receipt(op_data, output_path=None):
    """
    Генерирует PDF-чек для ручной операции.
    op_data: dict с полями: date, amount, type, bank, title, sender_name, receiver_phone, etc.
    """
    import controller
    
    if output_path is None:
        op_id = op_data.get("id", "unknown")[:12]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"receipt_{op_id}_{timestamp}.pdf"
    
    # Конфигурация
    config = controller.config
    nm = config.get("name") or {}
    sender_account = config.get("reki", {}).get("account", "40817810000112345678")

    op_type = (op_data.get("type") or "Debit").strip()
    if op_type == "Credit":
        display_sender = format_receipt_sender_short(
            (op_data.get("requisite_sender_name") or op_data.get("sender_name") or ""),
            nm,
        )
    else:
        # «Ирина К.» — имя + первая буква фамилии из конфига (как в Т‑Банке)
        fn = (nm.get("first_name") or "").strip()
        ln = (nm.get("last_name") or "").strip()
        if fn and ln:
            display_sender = f"{fn} {ln[0]}."
        else:
            display_sender = format_receipt_sender_short(nm.get("full_name", ""), nm)

    # Данные операции
    date_str = op_data.get("date", datetime.now().strftime("%d.%m.%Y, %H:%M:%S"))
    amount = abs(float(op_data.get("amount") or 0))
    bank = op_data.get("bank") or op_data.get("title") or "Перевод"
    receiver_name = op_data.get("title") or "Получатель"
    if op_type == "Credit":
        rp = (op_data.get("receipt_phone") or op_data.get("requisite_phone") or op_data.get("phone") or "").strip()
    else:
        rp = (op_data.get("requisite_phone") or op_data.get("phone") or "").strip()
    receiver_phone = format_phone_for_receipt(rp) or "+7 000 000-00-00"

    # Форматирование
    int_amount = int(amount)
    formatted_amount = f"{int_amount:,}".replace(",", " ")
    formatted_account = format_receipt_debit_account_line(sender_account)
    kvit = f"{random.randint(100,999)}-{random.randint(100,999)}-{random.randint(100,999)}"
    bukva = random.choice(["А", "В"])
    identificator = bukva + f"{random.randint(111111111111, 999999999999)}" + "406000004001" + f"{random.randint(11,99)}"
    identificator2 = f"{random.randint(11111, 99999)}"
    
    TEMPLATE_PATH = "sbpfinaltbanksend.pdf"
    FONT_NORMAL = "TinkoffSans-Regular.ttf"
    FONT_BOLD = "TinkoffSans-Medium.ttf"

    POSITIONS = {
        "date": (20, 86.5),
        "big_summ": (110, 92, 234.6, 142),
        "summ": (110, 173.98, 242.05, 187.98),
        "sender": (110, 214, 250, 228.18),
        "number": (110, 239-5, 250.02, 253.08-5),
        "name": (110, 239+20-5, 250.02, 253.08+20-5),
        "bank": (110, 239+40-5, 250.02, 253.08+40-5),
        "invoice": (110, 239+60-5, 250.02, 253.08+60-5),
        "identificator": (124.98999786376953, 314.00299072265625+8.18),
        "identificator2": (226.91000366210938, 325.0829772949219 + 8.18),
        "kvit": (93.35900115966797, 450.9629821777344 + 8.18),
    }

    doc = fitz.open(TEMPLATE_PATH)
    page = doc[0]

    def load_font(font_path, alias):
        if os.path.exists(font_path):
            page.insert_font(fontname=alias, fontfile=font_path)
            return alias
        return "helv"

    font_n = load_font(FONT_NORMAL, "my_normal")
    font_b = load_font(FONT_BOLD, "my_bold")

    page.insert_text(POSITIONS["date"], date_str.replace(",", ""), fontname=font_n, fontsize=8, color=(144/255, 144/255, 144/255))
    page.insert_textbox(POSITIONS["big_summ"], formatted_amount, fontname=font_b, fontsize=16, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["summ"], formatted_amount, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["sender"], display_sender, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["number"], receiver_phone, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["name"], receiver_name, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["bank"], bank, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_textbox(POSITIONS["invoice"], formatted_account, fontname=font_n, fontsize=9, color=(51/255, 51/255, 51/255), align=TEXT_ALIGN_RIGHT)
    page.insert_text(POSITIONS["identificator"], identificator, fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["identificator2"], identificator2, fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))
    page.insert_text(POSITIONS["kvit"], kvit, fontname=font_n, fontsize=9.008945, color=(51/255, 51/255, 51/255))

    doc.save(output_path, clean=True, deflate=True, garbage=4, pretty=False)
    doc.close()
    
    # Сжатие
    final_path = output_path.replace(".pdf", "_final.pdf")
    try:
        compress_pdf_ghostscript(output_path, final_path)
        return final_path
    except:
        return output_path




