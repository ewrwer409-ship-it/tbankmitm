from mitmproxy import http
import json
from urllib.parse import parse_qs
import sys
import os
import random
from pathlib import Path
from typing import Optional

sys.path.append(os.path.dirname(__file__))
import controller
import history
from bank_filter import (
    is_bank_flow,
    ensure_response_decoded,
    is_jsonish_response,
    flow_statements_spravki_context,
    url_prohibit_proxy_json_mutation,
)

ALLOWED_IPS = ["85.209.135.247", "5.18.160.29", "85.192.60.79", "127.0.0.1"]
PANEL_PORT = 8082
HOST_IP = os.environ.get("TBANKMITM_PUBLIC_IP", "85.209.135.247")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "yes", "true", "on")


_PANEL_ALLOW_ANY = _env_truthy("TBANKMITM_PANEL_ALLOW_ANY")


def _normalize_client_ip(raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith("::ffff:"):
        return raw[7:]
    return raw


def _client_ok_for_panel(client_ip: str) -> bool:
    """Доступ к /admin и API панели на порту 8082. Трафик банка (другие порты) не фильтруем."""
    if _PANEL_ALLOW_ANY:
        return True
    ip = _normalize_client_ip(client_ip)
    if ip in ALLOWED_IPS:
        return True
    if ip in ("127.0.0.1", "::1"):
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            second = int(ip.split(".")[1])
            if 16 <= second <= 31:
                return True
        except (ValueError, IndexError):
            pass
    return False


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _panel_request_path_only(req_path: str) -> str:
    q = req_path.find("?")
    return req_path[:q] if q >= 0 else req_path


def _panel_request_query_params(req_path: str) -> dict:
    q = req_path.find("?")
    if q < 0:
        return {}
    return parse_qs(req_path[q + 1 :])


def _resolve_manual_pdf_path(stored: str) -> Optional[str]:
    if not stored or not isinstance(stored, str):
        return None
    p = stored.strip()
    if os.path.isabs(p) and os.path.isfile(p):
        return p
    for base in (_BASE_DIR, os.getcwd()):
        cand = os.path.join(base, p)
        if os.path.isfile(cand):
            return cand
    cand = os.path.join(_BASE_DIR, os.path.basename(p))
    if os.path.isfile(cand):
        return cand
    return None


def effective_balance_value() -> float:
    """Тот же базовый баланс + корректировка ручных операций, что и в balance.py для подстановки в API."""
    base = float(((controller.config.get("balance") or {}).get("new_balance")) or 0)
    try:
        adj = history.compute_manual_balance_adjustment()
        return round(base + float(adj), 2)
    except Exception:
        return round(base, 2)


def random_data():
    first_names_male = ["Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Иван", "Владимир", "Михаил", "Николай"]
    first_names_female = ["Анна", "Елена", "Ольга", "Наталья", "Ирина", "Татьяна", "Светлана", "Мария", "Екатерина", "Юлия"]
    last_names_male = ["Иванов", "Петров", "Сидоров", "Смирнов", "Кузнецов", "Попов", "Васильев", "Павлов", "Семёнов", "Голубев"]
    last_names_female = ["Иванова", "Петрова", "Сидорова", "Смирнова", "Кузнецова", "Попова", "Васильева", "Павлова", "Семёнова", "Голубева"]
    middle_names_male = ["Александрович", "Дмитриевич", "Сергеевич", "Андреевич", "Владимирович", "Иванович", "Михайлович", "Николаевич", "Петрович", "Алексеевич"]
    middle_names_female = ["Александровна", "Дмитриевна", "Сергеевна", "Андреевна", "Владимировна", "Ивановна", "Михайловна", "Николаевна", "Петровна", "Алексеевна"]
    first_en_male = ["Alexander", "Dmitry", "Maxim", "Sergey", "Andrey", "Alexey", "Ivan", "Vladimir", "Mikhail", "Nikolay"]
    first_en_female = ["Anna", "Elena", "Olga", "Natalia", "Irina", "Tatiana", "Svetlana", "Maria", "Ekaterina", "Yulia"]
    last_en = ["Ivanov", "Petrov", "Sidorov", "Smirnov", "Kuznetsov", "Popov", "Vasiliev", "Pavlov", "Semenov", "Golubev"]
    middle_en_male = ["Alexandrovich", "Dmitrievich", "Sergeevich", "Andreevich", "Vladimirovich", "Ivanovich", "Mikhailovich", "Nikolaevich", "Petrovich", "Alexeevich"]
    middle_en_female = ["Alexandrovna", "Dmitrievna", "Sergeevna", "Andreevna", "Vladimirovna", "Ivanovna", "Mikhailovna", "Nikolaevna", "Petrovna", "Alexeevna"]

    is_male = random.choice([True, False])
    idx = random.randint(0, 9)
    if is_male:
        first_name = first_names_male[idx]
        last_name = last_names_male[idx]
        middle_name = middle_names_male[idx]
        first_en = first_en_male[idx]
        last_en_name = last_en[idx]
        middle_en = middle_en_male[idx]
        gender = "male"
        sex_code = "male"
    else:
        first_name = first_names_female[idx]
        last_name = last_names_female[idx]
        middle_name = middle_names_female[idx]
        first_en = first_en_female[idx]
        last_en_name = last_en[idx] + "a"  # упрощённо
        middle_en = middle_en_female[idx]
        gender = "female"
        sex_code = "female"

    full_name = f"{last_name} {first_name} {middle_name}"
    phone = f"+7{random.randint(900, 999)}{random.randint(100, 999)}{random.randint(10, 99)}{random.randint(10, 99)}"
    passport_series = f"{random.randint(1000, 9999)}"
    passport_number = f"{random.randint(100000, 999999)}"
    passport_issued_by = random.choice(["УФМС РОССИИ ПО Г. МОСКВЕ", "ГУ МВД ПО Г. САНКТ-ПЕТЕРБУРГУ", "ОТДЕЛОМ УФМС ПО КРАСНОДАРСКОМУ КРАЮ"])
    passport_issue_date = f"20{random.randint(10,25)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
    inn = f"{random.randint(100000000000, 999999999999)}"

    card1 = f"{random.randint(1000, 9999)}******{random.randint(1000, 9999)}"
    card2 = f"{random.randint(1000, 9999)}******{random.randint(1000, 9999)}"
    while card2 == card1:
        card2 = f"{random.randint(1000, 9999)}******{random.randint(1000, 9999)}"

    return {
        "name": {
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "full_name": full_name,
            "last_name_en": last_en_name,
            "first_name_en": first_en,
            "middle_name_en": middle_en,
            "phone": phone,
            "gender": gender,
            "sex_code": sex_code,
            "passport_series": passport_series,
            "passport_number": passport_number,
            "passport_issued_by": passport_issued_by,
            "passport_issue_date": passport_issue_date,
            "inn": inn
        },
        "reki": {
            "contract": str(random.randint(1000000000, 9999999999)),
            "account": f"40817810{random.randint(1000000000, 9999999999)}",
            "recipient": full_name,
            "beneficiary": f"Перевод средств по договору № {random.randint(1000000, 9999999)} {full_name} НДС не облагается"
        },
        "balance": {
            "new_balance": round(random.uniform(1000, 100000), 2),
            "new_card_number": card1,
            "new_card_number2": card2,
            "new_collect_sum": random.randint(1000, 100000)
        }
    }

HTML_PANEL = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover, user-scalable=yes">
    <title>T‑Bank Control Panel</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', sans-serif;
            background: #0b0b0b;
            min-height: 100vh;
            color: #ffe37a;
            padding: 8px;
        }
        .container { max-width: 560px; margin: 0 auto; }
        .header {
            background: #121212;
            border-radius: 20px;
            padding: 12px 14px;
            margin-bottom: 12px;
            border: 1px solid #2a2a2a;
            box-shadow: 0 4px 14px rgba(0,0,0,0.35);
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }
        .header h2 {
            font-weight: 600;
            font-size: 1.6rem;
            color: #ffd53d;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .header-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .header button {
            background: #ffd53d;
            border: 1px solid #ffd53d;
            color: #111;
            padding: 10px 18px;
            border-radius: 30px;
            font-weight: 500;
            font-size: 0.95rem;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: 0.2s;
            box-shadow: 0 2px 8px rgba(255,213,61,0.25);
        }
        .header button:hover { background: #ffe37a; transform: translateY(-2px); }
        .header button.secondary { background: #1f1f1f; color: #ffd53d; border-color: #3a3a3a; }
        .tabs {
            display: flex;
            gap: 6px;
            margin-bottom: 12px;
            flex-wrap: wrap;
            justify-content: center;
        }
        .tab {
            padding: 10px 18px;
            background: #121212;
            border: 1px solid #2f2f2f;
            border-radius: 30px;
            font-weight: 500;
            font-size: 0.95rem;
            color: #c7a83a;
            cursor: pointer;
            transition: 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.28);
        }
        .tab i { font-size: 1rem; }
        .tab:hover { background: #1c1c1c; color: #ffe37a; border-color: #ffd53d; }
        .tab.active { background: #ffd53d; color: #111; border-color: #ffd53d; }
        .tab-content {
            background: #121212;
            border-radius: 24px;
            padding: 14px 12px;
            border: 1px solid #2a2a2a;
            box-shadow: 0 4px 15px rgba(0,0,0,0.35);
        }
        .tab-pane { display: none; }
        .tab-pane.active { display: block; }
        .stats {
            display: flex;
            gap: 16px;
            margin-bottom: 24px;
            flex-wrap: wrap;
        }
        .stat-box {
            flex: 1 1 180px;
            background: #171717;
            border-radius: 18px;
            padding: 14px;
            border-left: 4px solid transparent;
        }
        .stat-box.income { border-left-color: #35c46a; }
        .stat-box.expense { border-left-color: #ffd53d; }
        .stat-label {
            font-size: 0.85rem;
            text-transform: uppercase;
            color: #c7a83a;
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .stat-value {
            font-size: 1.8rem;
            font-weight: 700;
            line-height: 1.2;
        }
        .stat-value.income .editable-stat { color: #48bb78; }
        .stat-value.expense .editable-stat { color: #ffd53d; }
        .stat-value .editable-stat {
            cursor: pointer;
            transition: 0.2s;
            display: inline-block;
            padding: 4px 8px;
            border-radius: 8px;
        }
        .stat-value .editable-stat:hover {
            background: rgba(0,0,0,0.05);
        }
        .stat-value .editable-stat.editing {
            cursor: text;
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .stat-edit-input {
            font-size: inherit;
            font-weight: inherit;
            font-family: inherit;
            background: white;
            border: 2px solid #ffd53d;
            border-radius: 8px;
            padding: 4px 8px;
            width: 180px;
            outline: none;
        }
        .manual-stat {
            font-style: italic;
            opacity: 0.8;
        }
        .stat-sub { color: #a0aec0; font-size: 0.8rem; }
        .filters {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
            align-items: center;
        }
        .search-wrapper {
            flex: 2;
            min-width: 200px;
            position: relative;
        }
        .search-wrapper i {
            position: absolute;
            left: 14px;
            top: 50%;
            transform: translateY(-50%);
            color: #a0aec0;
        }
        .search-wrapper input {
            width: 100%;
            padding: 12px 16px 12px 42px;
            background: #f7fafc;
            border: 1px solid #e2e8f0;
            border-radius: 30px;
            font-size: 0.95rem;
            outline: none;
        }
        .search-wrapper input:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
        }
        .filter-select {
            padding: 12px 20px;
            background: #f7fafc;
            border: 1px solid #e2e8f0;
            border-radius: 30px;
            font-size: 0.95rem;
            cursor: pointer;
            outline: none;
        }
        .btn {
            padding: 12px 22px;
            border: none;
            border-radius: 30px;
            font-weight: 500;
            font-size: 0.95rem;
            cursor: pointer;
            transition: 0.2s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            white-space: nowrap;
        }
        .btn-primary { background: #667eea; color: white; }
        .btn-primary:hover { background: #5a67d8; transform: translateY(-2px); }
        .btn-success { background: #48bb78; color: white; }
        .btn-success:hover { background: #38a169; transform: translateY(-2px); }
        .btn-danger { background: #f56565; color: white; }
        .btn-danger:hover { background: #e53e3e; transform: translateY(-2px); }
        .operations {
            max-height: 500px;
            overflow-y: auto;
            border-radius: 16px;
            background: #101010;
            padding: 6px;
        }
        .operation {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 18px;
            background: #181818;
            border-radius: 14px;
            margin-bottom: 8px;
            border: 1px solid #2f2f2f;
        }
        .operation.hidden { opacity: 0.5; background: #121212; filter: grayscale(0.5); }
        .op-info { flex: 1; }
        .op-date { font-size: 0.75rem; color: #aa9247; margin-bottom: 4px; }
        .op-desc { font-weight: 600; font-size: 0.95rem; }
        .op-meta-line { font-size: 0.8rem; color: #a0aec0; margin-top: 2px; }
        .op-meta-line.bank { color: #718096; }
        .op-meta-line.requisite { color: #c7a83a; }
        .op-amount {
            font-weight: 700;
            font-size: 1.1rem;
            margin-right: 16px;
            min-width: 100px;
            text-align: right;
        }
        .op-amount.income { color: #48bb78; }
        .op-amount.expense { color: #ffd53d; }
        .action-btn {
            background: #141414;
            border: 1px solid #3a3a3a;
            color: #d4b24a;
            width: 38px;
            height: 38px;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 1rem;
        }
        .action-btn:hover { background: #1e1e1e; }
        .form-group { margin-bottom: 18px; }
        .form-group label {
            display: block;
            margin-bottom: 6px;
            font-weight: 500;
            color: #d4b24a;
            font-size: 0.9rem;
        }
        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 12px 16px;
            background: #0f0f0f;
            border: 1px solid #3a3a3a;
            border-radius: 16px;
            font-size: 0.95rem;
            outline: none;
            transition: 0.2s;
            color: #ffe37a;
        }
        .form-group input:focus,
        .form-group textarea:focus,
        .form-group select:focus {
            border-color: #ffd53d;
            box-shadow: 0 0 0 3px rgba(255,213,61,0.18);
        }
        .form-row {
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }
        .form-row .form-group { flex: 1 1 200px; }
        .save-bar {
            margin-top: 24px;
            padding-top: 18px;
            border-top: 1px solid #2f2f2f;
            display: flex;
            justify-content: flex-end;
        }
        .section-title {
            font-size: 1.3rem;
            font-weight: 600;
            margin: 24px 0 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .manual-card {
            margin-bottom: 16px;
            padding: 14px;
            border-radius: 16px;
            background: linear-gradient(180deg, #ffe37a 0%, #ffd53d 100%);
            border: 1px solid #e8bd1c;
            box-shadow: 0 6px 14px rgba(0,0,0,0.10);
        }
        .manual-card .section-title { margin: 0 0 10px 0; color: #1b1b1b; }
        .manual-card .form-row { margin-bottom: 10px !important; padding: 0 !important; background: transparent !important; }
        .manual-card label { color: #1f1f1f; font-weight: 600; }
        .manual-card input, .manual-card select { background: #fffdf3; border-color: #d2a500; color: #1f1f1f; }
        .manual-card input:focus, .manual-card select:focus { border-color: #111; box-shadow: 0 0 0 3px rgba(17, 17, 17, 0.12); }
        .manual-card .btn { background: #111 !important; color: #ffd53d !important; border: 1px solid #111 !important; }
        .manual-card .btn:hover { background: #000 !important; color: #ffe37a !important; }
        .preset-logos { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; max-height: 120px; overflow-y: auto; -webkit-overflow-scrolling: touch; }
        .preset-chip {
            display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 999px;
            border: 1px solid #c59a00; background: #fff7cc; color: #1b1b1b; font-size: 0.82rem; cursor: pointer;
            user-select: none; transition: all .18s ease;
        }
        .preset-chip img { width: 18px; height: 18px; border-radius: 50%; object-fit: cover; background: #e2e8f0; }
        .preset-chip.active { background: #111; border-color: #111; color: #ffd53d; }
        .preset-chip.active img { box-shadow: 0 0 0 1px #ffd53d; }
        .manual-form-stack {
            display: flex;
            flex-direction: column;
            align-items: stretch !important;
            gap: 12px;
        }
        .manual-form-stack .form-group { flex: 1 1 auto !important; width: 100%; max-width: 100%; }
        .manual-form-stack .btn { width: 100%; justify-content: center; min-height: 48px; font-size: 1rem; }
        .conditional-field { display: none; }
        .search-wrapper input, .filter-select {
            background: #0f0f0f !important;
            border-color: #3a3a3a !important;
            color: #ffe37a !important;
        }
        @media (max-width: 700px) {
            body { padding: 6px; }
            .container { max-width: 100%; }
            .header { flex-direction: column; align-items: stretch; gap: 8px; border-radius: 14px; }
            .header h2 { font-size: 1.1rem; }
            .header-actions { justify-content: stretch; }
            .header-actions button { flex: 1; justify-content: center; padding: 10px 12px; }
            .tabs { justify-content: flex-start; overflow-x: auto; flex-wrap: nowrap; padding-bottom: 4px; }
            .tab { padding: 8px 12px; font-size: 0.82rem; white-space: nowrap; }
            .tab-pane { padding: 0; }
            .stats { gap: 10px; margin-bottom: 12px; }
            .stat-box { padding: 12px; border-radius: 12px; }
            .stat-value { font-size: 1.3rem; }
            .filters .btn { width: 100%; justify-content: center; }
            .filter-select, .search-wrapper input { width: 100%; }
            .operations { max-height: 56vh; }
            .operation { flex-wrap: wrap; gap: 8px; padding: 10px 12px; }
            .op-desc { font-size: 0.9rem; }
            .op-amount { min-width: auto; margin-right: 0; font-size: 1rem; }
            .action-btn { align-self: flex-end; width: 34px; height: 34px; border-radius: 10px; }
            .form-row { gap: 10px; }
            .form-group { margin-bottom: 12px; }
            .form-group input, .form-group select, .form-group textarea { padding: 10px 12px; border-radius: 12px; }
            .manual-card { padding: 10px; border-radius: 12px; }
            .toast {
                top: auto;
                bottom: max(12px, env(safe-area-inset-bottom));
                left: 50%;
                right: auto;
                transform: translate(-50%, 120px);
                max-width: calc(100vw - 24px);
                border-radius: 14px;
            }
            .toast.show { transform: translate(-50%, 0); }
        }
        .toast {
            position: fixed;
            background: #ffd53d;
            color: #111;
            padding: 12px 20px;
            border-radius: 30px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
            transition: transform 0.3s ease;
            z-index: 1000;
            font-weight: 500;
        }
        @media (min-width: 701px) {
            .toast {
                top: 16px;
                right: 16px;
                transform: translateX(400px);
            }
            .toast.show { transform: translateX(0); }
        }
        .placeholder { color: #a0aec0; font-style: italic; }
        .modal-overlay {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.72);
            z-index: 2000;
            align-items: flex-end;
            justify-content: center;
            padding: 12px;
            padding-bottom: max(12px, env(safe-area-inset-bottom));
        }
        .modal-overlay.show { display: flex; }
        .modal-sheet {
            background: #121212;
            border: 1px solid #3a3a3a;
            border-radius: 20px 20px 16px 16px;
            width: 100%;
            max-width: 520px;
            max-height: 88vh;
            overflow-y: auto;
            padding: 16px 14px 20px;
            box-shadow: 0 -8px 32px rgba(0,0,0,0.5);
        }
        .modal-sheet h3 {
            color: #ffd53d;
            font-size: 1.1rem;
            margin-bottom: 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-close {
            background: #1f1f1f;
            border: 1px solid #444;
            color: #ffe37a;
            width: 36px;
            height: 36px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 1.1rem;
        }
        .modal-actions { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
        .modal-actions .btn { flex: 1; justify-content: center; min-height: 46px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2><i class="fas fa-chart-line"></i> T‑Bank Panel</h2>
            <div class="header-actions">
                <button class="secondary" onclick="randomFillAndSave()"><i class="fas fa-dice"></i> Случайные</button>
                <button onclick="forceRefreshOps()"><i class="fas fa-sync-alt"></i> Обновить</button>
            </div>
        </div>

        <div class="tabs">
            <div class="tab active" onclick="switchTab('operations', this)"><i class="fas fa-chart-line"></i> Операции</div>
            <div class="tab" onclick="switchTab('requisites', this)"><i class="fas fa-credit-card"></i> Реквизиты</div>
            <div class="tab" onclick="switchTab('profile', this)"><i class="fas fa-user"></i> ФИО</div>
            <div class="tab" onclick="switchTab('documents', this)"><i class="fas fa-passport"></i> Документы</div>
            <div class="tab" onclick="switchTab('balance', this)"><i class="fas fa-coins"></i> Баланс</div>
            <div class="tab" onclick="switchTab('settings', this)"><i class="fas fa-cog"></i> Настройки</div>
        </div>

        <div id="operations" class="tab-pane active">
            <div class="stats">
                <div class="stat-box income">
                    <div class="stat-label"><i class="fas fa-arrow-up"></i> Доходы</div>
                    <div class="stat-value income" id="incomeAmount"><span class="editable-stat" onclick="editStat('income')">0 ₽</span></div>
                    <div class="stat-sub" id="incomeCount">0 оп.</div>
                </div>
                <div class="stat-box expense">
                    <div class="stat-label"><i class="fas fa-arrow-down"></i> Расходы</div>
                    <div class="stat-value expense" id="expenseAmount"><span class="editable-stat" onclick="editStat('expense')">0 ₽</span></div>
                    <div class="stat-sub" id="expenseCount">0 оп.</div>
                </div>
            </div>

            <div class="filters">
                <div class="search-wrapper">
                    <i class="fas fa-search"></i>
                    <input type="text" id="search" placeholder="Поиск по описанию...">
                </div>
                <select class="filter-select" id="typeFilter">
                    <option value="all">Все</option>
                    <option value="income">Доходы</option>
                    <option value="expense">Расходы</option>
                </select>
                <button class="btn btn-success" onclick="showAllOps()"><i class="fas fa-eye"></i> Показать все</button>
                <button class="btn btn-danger" onclick="hideAllOps()"><i class="fas fa-eye-slash"></i> Скрыть видимые</button>
                <button class="btn btn-danger" style="opacity:0.95;" onclick="hideEntireHistoryOps()" title="Скрыть все операции в кэше прокси (банк + ручные)"><i class="fas fa-ban"></i> Скрыть всю историю</button>
            </div>

            <div class="manual-card">
                <div class="section-title" style="font-size:1.05rem;"><i class="fas fa-university"></i> Переводы в другие банки / из других банков</div>
                <div class="form-row manual-form-stack" style="align-items:stretch;">
                    <div class="form-group" style="flex:1 1 180px;">
                        <label>Направление</label>
                        <select id="manual_direction">
                            <option value="out">В другой банк (расход)</option>
                            <option value="in">Из другого банка (доход)</option>
                        </select>
                    </div>
                    <div class="form-group" style="flex:1 1 150px;">
                        <label>Сумма, ₽</label>
                        <input type="number" id="manual_amount" step="0.01" min="0" placeholder="3008.95">
                    </div>
                    <div class="form-group" style="flex:1 1 240px;">
                        <label>Банк / логотип</label>
                        <select id="manual_bank_preset">
                            <option value="sbp">СБП</option>
                            <option value="sber">Сбербанк</option>
                            <option value="tbank">Т-Банк</option>
                            <option value="alfa">Альфа-Банк</option>
                            <option value="vtb">ВТБ</option>
                            <option value="psb">ПСБ</option>
                            <option value="yandex">Яндекс Банк</option>
                            <option value="wb">WB Банк</option>
                            <option value="sovcom">Совкомбанк</option>
                            <option value="akbars">Ак Барс Банк</option>
                            <option value="mts">МТС Деньги</option>
                            <option value="amobayl">Неизвестный (amobayl)</option>
                            <option value="eskhata">Эсхата Банк</option>
                            <option value="uralsib">Уралсиб</option>
                            <option value="fora">Фора-Банк</option>
                            <option value="genbank">Генбанк</option>
                            <option value="abs_rossiya">АБ Россия</option>
                            <option value="cupis">ЦУПИС</option>
                            <option value="rncb">РНКБ</option>
                            <option value="akcept">Банк Акцепт</option>
                            <option value="domrf">Дом.РФ</option>
                            <option value="ubrir">УБРИР</option>
                            <option value="crediteurope">Кредит Европа Банк (Россия)</option>
                            <option value="pochtabank">Почта Банк</option>
                            <option value="cifra">Цифра банк</option>
                            <option value="spitamen">Спитамен Банк</option>
                            <option value="mkb">МКБ</option>
                            <option value="rshb">РСХБ</option>
                            <option value="primbank">Банк Приморье</option>
                            <option value="primsocbank">СКБ Примсоцбанк</option>
                            <option value="bankspb">Банк Санкт-Петербург</option>
                            <option value="rocketbank">Рокетбанк</option>
                            <option value="raiffeisen">Райффайзен Банк</option>
                            <option value="mbb">МББ Банк</option>
                            <option value="centrinvest">Центр-инвест</option>
                            <option value="octo">Окто-банк</option>
                            <option value="yoomoney">ЮMoney</option>
                            <option value="gazprom">Газпромбанк</option>
                            <option value="mts_bank">МТС Банк</option>
                            <option value="ozon">Озон Банк</option>
                            <option value="otp">ОТП Банк</option>
                            <option value="custom">Свой (без пресета)</option>
                        </select>
                        <div class="preset-logos" id="presetLogos"></div>
                    </div>
                    <div class="form-group" style="flex:1 1 190px;">
                        <label>Подпись банка (опционально)</label>
                        <input type="text" id="manual_bank" placeholder="Например: ВТБ">
                    </div>
                    <div class="form-group" style="flex:1 1 210px;">
                        <label>Дата и время</label>
                        <input type="datetime-local" id="manual_datetime">
                    </div>
                </div>
                <div class="form-row manual-form-stack" style="align-items:stretch;">
                    <div class="form-group" style="flex:1 1 220px;">
                        <label>ФИО / имя в приложении</label>
                        <input type="text" id="manual_title" placeholder="Светлана Д.">
                    </div>
                    <div class="form-group" style="flex:1 1 220px;">
                        <label>2-я строка (категория)</label>
                        <input type="text" id="manual_subtitle" placeholder="Переводы">
                    </div>
                    <div class="form-group" id="manual_sender_name_row" style="flex:1 1 220px;">
                        <label>Реквизиты: ФИО отправителя (для пополнения / в чеке дохода)</label>
                        <input type="text" id="manual_sender_name" placeholder="Козлова Ирина Николаевна">
                    </div>
                    <div class="form-group" id="manual_receipt_phone_row" style="flex:1 1 220px;display:none;">
                        <label>Телефон в чеке (пополнение из другого банка)</label>
                        <input type="tel" id="manual_receipt_phone" placeholder="+79161234567">
                    </div>
                    <div class="form-group" id="manual_recipient_type_row" style="flex:1 1 220px;">
                        <label>Получатель</label>
                        <select id="manual_recipient_type">
                            <option value="phone">По телефону</option>
                            <option value="card">По номеру карты</option>
                        </select>
                    </div>
                    <div class="form-group" id="manual_phone_row" style="flex:1 1 220px;">
                        <label>Реквизиты: номер телефона (для перевода)</label>
                        <input type="tel" id="manual_phone" placeholder="+79991234567">
                    </div>
                    <div class="form-group conditional-field" id="manual_card_row" style="flex:1 1 220px;">
                        <label>Номер карты получателя</label>
                        <input type="text" id="manual_card_number" placeholder="2200******1234">
                    </div>
                    <div class="form-group" style="flex:2 1 260px;">
                        <label>Комментарий (необязательно)</label>
                        <input type="text" id="manual_desc" placeholder="">
                    </div>
                    <div class="form-group" style="flex:0 1 auto;">
                        <label style="opacity:0;">.</label>
                        <button type="button" class="btn btn-success" onclick="addManualOperation()"><i class="fas fa-plus"></i> Добавить новую</button>
                    </div>
                </div>
            </div>

            <div class="operations" id="operationsList">
                <div style="text-align: center; padding: 40px; color: #a0aec0;">Загрузка...</div>
            </div>
        </div>

        <div id="requisites" class="tab-pane">
            <div class="section-title"><i class="fas fa-credit-card"></i> Реквизиты</div>
            <div class="form-group"><label>Номер договора</label><input type="text" id="reki_contract" placeholder="10 цифр, например 7777777777"></div>
            <div class="form-group"><label>Номер счета</label><input type="text" id="reki_account" placeholder="20 цифр, например 40817810799999999999"></div>
            <div class="form-group"><label>ФИО получателя</label><input type="text" id="reki_recipient" placeholder="ИВАНОВ ИВАН ИВАНОВИЧ"></div>
            <div class="form-group"><label>Назначение платежа</label><textarea id="reki_beneficiary" rows="2" placeholder="Перевод средств по договору..."></textarea></div>
            <div class="form-group"><label>Номер карты (основная)</label><input type="text" id="balance_card" placeholder="9999******9999"></div>
            <div class="form-group"><label>Номер карты (вторая)</label><input type="text" id="balance_card2" placeholder="9999******9999"></div>
            <div class="save-bar"><button class="btn btn-primary" onclick="saveRequisites()"><i class="fas fa-save"></i> Сохранить</button></div>
        </div>

        <div id="profile" class="tab-pane">
            <div class="section-title"><i class="fas fa-user"></i> ФИО</div>
            <div class="form-row">
                <div class="form-group"><label>Фамилия (рус)</label><input type="text" id="name_last" placeholder="ИВАНОВ"></div>
                <div class="form-group"><label>Имя (рус)</label><input type="text" id="name_first" placeholder="ИВАН"></div>
                <div class="form-group"><label>Отчество (рус)</label><input type="text" id="name_middle" placeholder="ИВАНОВИЧ"></div>
            </div>
            <div class="form-group"><label>Полное имя (рус)</label><input type="text" id="name_full" placeholder="ИВАНОВ ИВАН ИВАНОВИЧ"></div>
            <div class="form-row">
                <div class="form-group"><label>Фамилия (лат)</label><input type="text" id="name_last_en" placeholder="IVANOV"></div>
                <div class="form-group"><label>Имя (лат)</label><input type="text" id="name_first_en" placeholder="IVAN"></div>
                <div class="form-group"><label>Отчество (лат)</label><input type="text" id="name_middle_en" placeholder="IVANOVICH"></div>
            </div>
            <div class="form-group"><label>Телефон</label><input type="text" id="name_phone" placeholder="+7XXXXXXXXXX"></div>
            <div class="form-group"><label>Email (подмена в JSON ответах Т‑Банка)</label><input type="email" id="name_email" placeholder="user@example.com"></div>
            <div class="form-row">
                <div class="form-group"><label>Пол</label>
                    <select id="name_gender"><option value="male">Мужской</option><option value="female">Женский</option></select>
                </div>
                <div class="form-group"><label>Код пола</label><input type="text" id="name_sex_code" placeholder="male/female"></div>
            </div>
            <div class="save-bar"><button class="btn btn-primary" onclick="saveName()"><i class="fas fa-save"></i> Сохранить</button></div>
        </div>

        <div id="documents" class="tab-pane">
            <div class="section-title"><i class="fas fa-passport"></i> Документы</div>
            <div class="form-row">
                <div class="form-group"><label>Серия паспорта</label><input type="text" id="passport_series" placeholder="1212"></div>
                <div class="form-group"><label>Номер паспорта</label><input type="text" id="passport_number" placeholder="345678"></div>
            </div>
            <div class="form-group"><label>Кем выдан</label><input type="text" id="passport_issued" placeholder="TESTOVOE UVD"></div>
            <div class="form-group"><label>Дата выдачи</label><input type="date" id="passport_date"></div>
            <div class="form-group"><label>ИНН</label><input type="text" id="inn" placeholder="123456789012"></div>
            <div class="save-bar"><button class="btn btn-primary" onclick="saveDocuments()"><i class="fas fa-save"></i> Сохранить</button></div>
        </div>

        <div id="balance" class="tab-pane">
            <div class="section-title"><i class="fas fa-coins"></i> Баланс</div>
            <div class="form-group"><label>Новый баланс</label><input type="number" id="balance_amount" step="0.01" placeholder="9999.99"></div>
            <div class="form-group"><label>Сумма сбора</label><input type="number" id="balance_collect" placeholder="9999"></div>
            <div class="save-bar"><button class="btn btn-primary" onclick="saveBalance()"><i class="fas fa-save"></i> Сохранить</button></div>
        </div>

        <div id="settings" class="tab-pane">
            <div class="section-title"><i class="fas fa-cog"></i> Настройки</div>
            <div class="form-group"><label>Показывать категории</label>
                <select id="show_categories"><option value="true">Да</option><option value="false">Нет</option></select>
            </div>
            <div class="form-group"><label>Сортировка</label>
                <select id="sort_direction"><option value="desc">Сначала новые</option><option value="asc">Сначала старые</option></select>
            </div>
            <div class="form-group"><label>Список операций в панели</label>
                <select id="panel_show_all_operations">
                    <option value="true">Все из кэша прокси (до ~1200) — скрытие обновляет суммы</option>
                    <option value="false">Только текущий календарный месяц</option>
                </select>
            </div>
            <div class="form-group"><label>Доходы и расходы на панели (сводка)</label>
                <select id="panel_sync_bank_histogram">
                    <option value="true">Как на сайте Т‑Банка при открытии /mybank через прокси</option>
                    <option value="false">Только сумма операций в прокси (кэш + ручные)</option>
                </select>
            </div>
            <div class="form-group"><label>Если сводка отключена — логика графика</label>
                <select id="histogram_sync_with_operations">
                    <option value="true">По операциям в прокси</option>
                    <option value="false">Только ручные суммы (клик по доходам/расходам)</option>
                </select>
            </div>
            <div class="save-bar"><button class="btn btn-primary" onclick="saveSettings()"><i class="fas fa-save"></i> Сохранить</button></div>
        </div>
    </div>

    <div class="toast" id="toast">Сохранено</div>

    <div class="modal-overlay" id="editManualModal" onclick="if(event.target===this) closeEditManualModal()">
        <div class="modal-sheet" onclick="event.stopPropagation()">
            <h3><span>Редактировать операцию</span><button type="button" class="modal-close" onclick="closeEditManualModal()" title="Закрыть">&times;</button></h3>
            <input type="hidden" id="edit_manual_id" value="">
            <div class="form-group">
                <label>Направление</label>
                <select id="edit_direction">
                    <option value="out">В другой банк (расход)</option>
                    <option value="in">Из другого банка (доход)</option>
                </select>
            </div>
            <div class="form-group"><label>Сумма, ₽</label><input type="number" id="edit_amount" step="0.01" min="0"></div>
            <div class="form-group"><label>Банк / пресет</label>
                <select id="edit_bank_preset">
                    <option value="sbp">СБП</option><option value="sber">Сбербанк</option><option value="tbank">Т-Банк</option>
                    <option value="alfa">Альфа-Банк</option><option value="vtb">ВТБ</option><option value="psb">ПСБ</option>
                    <option value="yandex">Яндекс Банк</option><option value="wb">WB Банк</option>
                    <option value="sovcom">Совкомбанк</option><option value="akbars">Ак Барс Банк</option>
                    <option value="mts">МТС Деньги</option><option value="amobayl">Неизвестный (amobayl)</option><option value="eskhata">Эсхата Банк</option>
                    <option value="uralsib">Уралсиб</option><option value="fora">Фора-Банк</option><option value="genbank">Генбанк</option>
                    <option value="abs_rossiya">АБ Россия</option><option value="cupis">ЦУПИС</option><option value="rncb">РНКБ</option>
                    <option value="akcept">Банк Акцепт</option><option value="domrf">Дом.РФ</option><option value="ubrir">УБРИР</option>
                    <option value="crediteurope">Кредит Европа Банк (Россия)</option><option value="pochtabank">Почта Банк</option><option value="cifra">Цифра банк</option>
                    <option value="spitamen">Спитамен Банк</option><option value="mkb">МКБ</option><option value="rshb">РСХБ</option>
                    <option value="primbank">Банк Приморье</option><option value="primsocbank">СКБ Примсоцбанк</option><option value="bankspb">Банк Санкт-Петербург</option>
                    <option value="rocketbank">Рокетбанк</option><option value="raiffeisen">Райффайзен Банк</option><option value="mbb">МББ Банк</option>
                    <option value="centrinvest">Центр-инвест</option><option value="octo">Окто-банк</option><option value="yoomoney">ЮMoney</option>
                    <option value="gazprom">Газпромбанк</option><option value="mts_bank">МТС Банк</option><option value="ozon">Озон Банк</option><option value="otp">ОТП Банк</option><option value="custom">Свой</option>
                </select>
            </div>
            <div class="form-group"><label>Подпись банка</label><input type="text" id="edit_bank" placeholder="ВТБ"></div>
            <div class="form-group"><label>Дата и время</label><input type="datetime-local" id="edit_datetime"></div>
            <div class="form-group"><label>ФИО / имя в приложении</label><input type="text" id="edit_title"></div>
            <div class="form-group"><label>2-я строка</label><input type="text" id="edit_subtitle"></div>
            <div class="form-group" id="edit_sender_name_row"><label>Реквизиты: ФИО отправителя (для пополнения)</label><input type="text" id="edit_sender_name" placeholder="Светлана Д."></div>
            <div class="form-group" id="edit_recipient_type_row"><label>Получатель</label>
                <select id="edit_recipient_type">
                    <option value="phone">По телефону</option>
                    <option value="card">По номеру карты</option>
                </select>
            </div>
            <div class="form-group" id="edit_phone_row"><label>Реквизиты: номер телефона (для перевода)</label><input type="tel" id="edit_phone" placeholder="+79991234567"></div>
            <div class="form-group" id="edit_receipt_phone_row" style="display:none;"><label>Телефон в чеке (доход из банка)</label><input type="tel" id="edit_receipt_phone" placeholder="+79161234567"></div>
            <div class="form-group conditional-field" id="edit_card_row"><label>Номер карты получателя</label><input type="text" id="edit_card_number" placeholder="2200******1234"></div>
            <div class="form-group"><label>Комментарий</label><input type="text" id="edit_desc"></div>
            <div class="modal-actions">
                <button type="button" class="btn btn-primary" onclick="saveManualEdit()"><i class="fas fa-save"></i> Сохранить</button>
                <button type="button" class="btn btn-danger" onclick="closeEditManualModal()">Отмена</button>
            </div>
        </div>
    </div>

    <script>
        const BANK_PRESET_META = {
            sbp: {name: 'СБП', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdHJhbnNmZXJzLWMxLnBuZw=='},
            sber: {name: 'Сбербанк', logo: 'https://brands-prod.cdn-tinkoff.ru/general_logo/sber.png'},
            tbank: {name: 'Т-Банк', logo: 'https://brands-prod.cdn-tinkoff.ru/general_logo/tinkoff-new.png'},
            alfa: {name: 'Альфа-Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWxmYWJhbmsucG5n'},
            vtb: {name: 'ВТБ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdnRiYmFuay5wbmc='},
            psb: {name: 'ПСБ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJvbXN2eWF6YmFuay5wbmc='},
            yandex: {name: 'Яндекс Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veWEtYmFuay5wbmc='},
            wb: {name: 'WB Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vd2lsZGJlcnJpZXMtYmFuay5wbmc='},
            sovcom: {name: 'Совкомбанк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc292Y29tYmFuay5wbmc='},
            akbars: {name: 'Ак Барс Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiLnBuZw=='},
            mts: {name: 'МТС Деньги', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzZGVuZ2kucG5n'},
            amobayl: {name: 'Неизвестный (amobayl)', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYW1vYmF5bC5wbmc='},
            eskhata: {name: 'Эсхата Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZXNraGF0YS1uZXcucG5n'},
            uralsib: {name: 'Уралсиб', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdXJhbHNpYi5wbmc='},
            fora: {name: 'Фора-Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZm9yYWJhbmsucG5n'},
            genbank: {name: 'Генбанк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2VuYmFuay1uZXcucG5n'},
            abs_rossiya: {name: 'АБ Россия', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtiX3Jvc3NpeWEucG5n'},
            cupis: {name: 'ЦУПИС', logo: 'https://bms-logo-prod.t-static.ru/general_logo/1cupis-mplat.png'},
            rncb: {name: 'РНКБ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm5jYi5wbmc='},
            akcept: {name: 'Банк Акцепт', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYWtjZXB0LnBuZw=='},
            domrf: {name: 'Дом.РФ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZG9tcmZiYW5rLnBuZw=='},
            ubrir: {name: 'УБРИР', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vdWJyaXIucG5n'},
            crediteurope: {name: 'Кредит Европа Банк (Россия)', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28va3JlZGl0LWV2cm9wYS1iYW5rLnBuZw=='},
            pochtabank: {name: 'Почта Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcG9jaHRhLWJhbmsucG5n'},
            cifra: {name: 'Цифра банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2lmcmEtYmFuay5wbmc='},
            spitamen: {name: 'Спитамен Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vc3BpdGFtZW5iYW5rLnBuZw=='},
            mkb: {name: 'МКБ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWtiLW5ldy0yLnBuZw=='},
            rshb: {name: 'РСХБ', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcm9zc2VsaG96YmFuay5wbmc='},
            primbank: {name: 'Банк Приморье', logo: 'https://bms-logo-prod.t-static.ru/general_logo/primbank-new.png'},
            primsocbank: {name: 'СКБ Примсоцбанк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcHJpbS1zb2MtYmFuay5wbmc='},
            bankspb: {name: 'Банк Санкт-Петербург', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vYmFua3NwYi5wbmc='},
            rocketbank: {name: 'Рокетбанк', logo: 'https://bms-logo-prod.t-static.ru/general_logo/rocketbank.png'},
            raiffeisen: {name: 'Райффайзен Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vcmFpZmZlaXNlbi5wbmc='},
            mbb: {name: 'МББ Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbWJicnUucG5n'},
            centrinvest: {name: 'Центр-инвест', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vY2VudHJpbnZlc3QucG5n'},
            octo: {name: 'Окто-банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb2N0by1iYW5rLnBuZw=='},
            yoomoney: {name: 'ЮMoney', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28veW9vbW9uZXkucG5n'},
            gazprom: {name: 'Газпромбанк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vZ2F6cHJvbWJhbmsucG5n'},
            mts_bank: {name: 'МТС Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vbXRzLWJhbmsucG5n'},
            ozon: {name: 'Озон Банк', logo: 'https://brands-prod.cdn-tinkoff.ru/general_logo/finance-ozon-2.png'},
            otp: {name: 'ОТП Банк', logo: 'https://imgproxy.cdn-tinkoff.ru/compressed95/aHR0cHM6Ly9icmFuZHMtcHJvZC5jZG4tdGlua29mZi5ydS9nZW5lcmFsX2xvZ28vb3RwYmFuay1uZXcucG5n'}
        };
        let operations = [];
        let hiddenOps = new Set();
        let configLoaded = false;

        function renderPresetLogos() {
            const box = document.getElementById('presetLogos');
            const select = document.getElementById('manual_bank_preset');
            if (!box || !select) return;
            const selected = select.value;
            let html = '';
            const toImgproxy = (url) => {
                if (!url) return '';
                if (url.includes('imgproxy.cdn-tinkoff.ru/compressed95/')) return url;
                if (!url.includes('brands-prod.cdn-tinkoff.ru/') && !url.includes('brands-static.cdn-tinkoff.ru/')) return url;
                try {
                    const utf8 = unescape(encodeURIComponent(url));
                    return 'https://imgproxy.cdn-tinkoff.ru/compressed95/' + btoa(utf8);
                } catch (_) {
                    return url;
                }
            };
            Object.entries(BANK_PRESET_META).forEach(([key, item]) => {
                html += `<button type="button" class="preset-chip ${selected===key?'active':''}" onclick="setBankPreset('${key}')"><img src="${toImgproxy(item.logo)}" alt=""><span>${item.name}</span></button>`;
            });
            html += `<button type="button" class="preset-chip ${selected==='custom'?'active':''}" onclick="setBankPreset('custom')"><span>Свой</span></button>`;
            box.innerHTML = html;
        }

        function setBankPreset(preset) {
            const select = document.getElementById('manual_bank_preset');
            if (!select) return;
            select.value = preset;
            renderPresetLogos();
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.classList.add('show');
            setTimeout(() => toast.classList.remove('show'), 1500);
        }

        function switchTab(tabId, el) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            if (el) el.classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        function bankDateToDatetimeLocal(d) {
            if (!d) return '';
            const m = String(d).match(/(\d{2})\.(\d{2})\.(\d{4}),\s*(\d{2}):(\d{2})(?::(\d{2}))?/);
            if (!m) return '';
            const sec = m[6] ? m[6] : '00';
            return m[3] + '-' + m[2] + '-' + m[1] + 'T' + m[4] + ':' + m[5] + ':' + sec;
        }

        function syncRecipientTypeWithBankPreset(prefix) {
            const presetEl = document.getElementById(prefix + '_bank_preset');
            const typeEl = document.getElementById(prefix + '_recipient_type');
            if (!presetEl || !typeEl) return;
            typeEl.value = presetEl.value === 'sbp' ? 'phone' : 'card';
            updateDirectionSpecificFields(prefix);
        }

        function updateRecipientFields(prefix) {
            const typeEl = document.getElementById(prefix + '_recipient_type');
            const phoneRow = document.getElementById(prefix + '_phone_row');
            const cardRow = document.getElementById(prefix + '_card_row');
            if (!typeEl || !phoneRow || !cardRow) return;
            phoneRow.style.display = 'block';
            cardRow.style.display = 'block';
        }

        function updateDirectionSpecificFields(prefix) {
            const directionEl = document.getElementById(prefix + '_direction');
            const recipientTypeRow = document.getElementById(prefix + '_recipient_type_row');
            const senderNameRow = document.getElementById(prefix + '_sender_name_row');
            const phoneRow = document.getElementById(prefix + '_phone_row');
            const cardRow = document.getElementById(prefix + '_card_row');
            const receiptRow = document.getElementById(prefix + '_receipt_phone_row');
            if (!directionEl || !recipientTypeRow || !senderNameRow || !phoneRow || !cardRow) return;
            const isIncoming = directionEl.value === 'in';
            senderNameRow.style.display = 'block';
            recipientTypeRow.style.display = isIncoming ? 'none' : 'block';
            if (receiptRow) receiptRow.style.display = isIncoming ? 'block' : 'none';
            if (isIncoming) {
                phoneRow.style.display = 'none';
                cardRow.style.display = 'none';
            } else {
                phoneRow.style.display = 'block';
                updateRecipientFields(prefix);
            }
        }

        function openEditManualModal(op) {
            if (!op || !op.manual || !op.id) return;
            document.getElementById('edit_manual_id').value = op.id;
            document.getElementById('edit_direction').value = op.type === 'Credit' ? 'in' : 'out';
            document.getElementById('edit_amount').value = Math.abs(op.amount || 0);
            const preset = (op.bank_preset || 'custom').toLowerCase();
            const sel = document.getElementById('edit_bank_preset');
            if ([...sel.options].some(o => o.value === preset)) sel.value = preset;
            else sel.value = 'custom';
            document.getElementById('edit_bank').value = op.bank || '';
            document.getElementById('edit_datetime').value = bankDateToDatetimeLocal(op.date).slice(0, 16);
            document.getElementById('edit_title').value = op.title || '';
            document.getElementById('edit_subtitle').value = op.subtitle || '';
            document.getElementById('edit_phone').value = op.requisite_phone || op.phone || '';
            const erp = document.getElementById('edit_receipt_phone');
            if (erp) erp.value = op.receipt_phone || '';
            document.getElementById('edit_card_number').value = op.card_number || '';
            document.getElementById('edit_sender_name').value = op.requisite_sender_name || op.sender_name || '';
            document.getElementById('edit_recipient_type').value = (op.requisite_phone || op.phone) ? 'phone' : 'card';
            updateDirectionSpecificFields('edit');
            document.getElementById('edit_desc').value = op.description || '';
            const modal = document.getElementById('editManualModal');
            modal.classList.add('show');
        }

        function closeEditManualModal() {
            document.getElementById('editManualModal').classList.remove('show');
        }

        function saveManualEdit() {
            const id = document.getElementById('edit_manual_id').value;
            if (!id) return;
            const dt = document.getElementById('edit_datetime').value;
            const recipientType = document.getElementById('edit_recipient_type').value;
            const direction = document.getElementById('edit_direction').value;
            const requisitePhone = (document.getElementById('edit_phone').value || '').trim();
            const requisiteSenderName = (document.getElementById('edit_sender_name').value || '').trim();
            fetch('/api/operations/update', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    id: id,
                    direction: direction,
                    amount: parseFloat(document.getElementById('edit_amount').value) || 0,
                    bank_preset: document.getElementById('edit_bank_preset').value,
                    bank: document.getElementById('edit_bank').value,
                    datetime: dt ? dt + (dt.length === 16 ? ':00' : '') : null,
                    title: document.getElementById('edit_title').value,
                    subtitle: document.getElementById('edit_subtitle').value,
                    phone: requisitePhone,
                    requisite_phone: requisitePhone,
                    card_number: direction === 'out' && recipientType === 'card' ? document.getElementById('edit_card_number').value : '',
                    sender_name: requisiteSenderName,
                    requisite_sender_name: requisiteSenderName,
                    receipt_phone: direction === 'in' ? (document.getElementById('edit_receipt_phone').value || '').trim() : '',
                    description: document.getElementById('edit_desc').value
                })
            })
            .then(r => r.json())
            .then(d => {
                if (d.error) throw new Error(d.error);
                showToast('Сохранено');
                closeEditManualModal();
                loadOperations();
            })
            .catch(() => showToast('Ошибка сохранения'));
        }

        function forceRefreshOps() {
            loadOperations();
        }

        function loadAllData() {
            loadOperations();
            renderPresetLogos();
            if (!configLoaded) { loadConfig(); configLoaded = true; }
        }

        function editStat(type) {
            const span = document.getElementById(type === 'income' ? 'incomeAmount' : 'expenseAmount').querySelector('.editable-stat');
            const currentText = span.innerText.replace(/[^\d.,\-+]/g, '').replace(',', '.');
            const currentValue = parseFloat(currentText) || 0;
            const input = document.createElement('input');
            input.type = 'number';
            input.step = '0.01';
            input.value = currentValue;
            input.className = 'stat-edit-input';
            input.onblur = function() {
                finishEdit(type, input.value);
            };
            input.onkeypress = function(e) {
                if (e.key === 'Enter') {
                    finishEdit(type, input.value);
                }
            };
            span.innerHTML = '';
            span.appendChild(input);
            input.focus();
            span.classList.add('editing');
        }

        function finishEdit(type, value) {
            const span = document.getElementById(type === 'income' ? 'incomeAmount' : 'expenseAmount').querySelector('.editable-stat');
            const numValue = parseFloat(value) || 0;
            span.innerHTML = numValue.toLocaleString() + ' ₽';
            span.classList.remove('editing');
            // Сохраняем новое значение на сервер
            saveManualStat(type, numValue);
        }

        function saveManualStat(type, value) {
            let payload = {};
            if (type === 'income') {
                payload.income = value;
            } else {
                payload.expense = value;
            }
            fetch('/api/set_manual_stats', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            })
            .then(r => r.json())
            .then(() => {
                showToast('✏️ Ручное изменение сохранено');
                loadOperations();
                loadConfig();
            })
            .catch(() => showToast('❌ Ошибка'));
        }

        function resetManual(type) {
            let payload = {};
            if (type === 'income') {
                payload.income = null;
            } else {
                payload.expense = null;
            }
            fetch('/api/set_manual_stats', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            })
            .then(() => {
                showToast('↺ Сброшено');
                loadOperations();
            })
            .catch(() => showToast('❌ Ошибка'));
        }

        function loadOperations() {
            fetch('/api/operations')
                .then(r => r.json())
                .then(data => {
                    operations = data.operations || [];
                    hiddenOps = new Set(data.hidden || []);
                    document.getElementById('incomeAmount').querySelector('.editable-stat').innerHTML = data.stats.income.toLocaleString() + ' ₽';
                    document.getElementById('expenseAmount').querySelector('.editable-stat').innerHTML = data.stats.expense.toLocaleString() + ' ₽';
                    document.getElementById('incomeCount').innerText = data.stats.income_count + ' оп.';
                    document.getElementById('expenseCount').innerText = data.stats.expense_count + ' оп.';
                    filterOps();
                })
                .catch(() => document.getElementById('operationsList').innerHTML = '<div style="padding:40px;text-align:center;">Ошибка</div>');
        }

        function loadConfig() {
            fetch('/api/config').then(r => r.json()).then(config => {
                if (config.reki) {
                    document.getElementById('reki_contract').value = config.reki.contract || '';
                    document.getElementById('reki_account').value = config.reki.account || '';
                    document.getElementById('reki_recipient').value = config.reki.recipient || '';
                    document.getElementById('reki_beneficiary').value = config.reki.beneficiary || '';
                }
                if (config.balance) {
                    document.getElementById('balance_card').value = config.balance.new_card_number || '';
                    document.getElementById('balance_card2').value = config.balance.new_card_number2 || '';
                    document.getElementById('balance_amount').value = config.balance.new_balance || '';
                    document.getElementById('balance_collect').value = config.balance.new_collect_sum || '';
                }
                if (config.name) {
                    document.getElementById('name_last').value = config.name.last_name || '';
                    document.getElementById('name_first').value = config.name.first_name || '';
                    document.getElementById('name_middle').value = config.name.middle_name || '';
                    document.getElementById('name_full').value = config.name.full_name || '';
                    document.getElementById('name_last_en').value = config.name.last_name_en || '';
                    document.getElementById('name_first_en').value = config.name.first_name_en || '';
                    document.getElementById('name_middle_en').value = config.name.middle_name_en || '';
                    document.getElementById('name_phone').value = config.name.phone || '';
                    document.getElementById('name_email').value = config.name.email || '';
                    document.getElementById('name_gender').value = config.name.gender || 'male';
                    document.getElementById('name_sex_code').value = config.name.sex_code || 'male';
                    document.getElementById('passport_series').value = config.name.passport_series || '';
                    document.getElementById('passport_number').value = config.name.passport_number || '';
                    document.getElementById('passport_issued').value = config.name.passport_issued_by || '';
                    document.getElementById('passport_date').value = config.name.passport_issue_date || '';
                    document.getElementById('inn').value = config.name.inn || '';
                }
                if (config.history) {
                    document.getElementById('show_categories').value = config.history.show_categories ? 'true' : 'false';
                    document.getElementById('sort_direction').value = config.history.sort_direction || 'desc';
                    const pa = document.getElementById('panel_show_all_operations');
                    if (pa) pa.value = config.history.panel_show_all_operations === false ? 'false' : 'true';
                }
                if (config.manual) {
                    const ps = document.getElementById('panel_sync_bank_histogram');
                    if (ps) ps.value = config.manual.panel_sync_bank_histogram === false ? 'false' : 'true';
                    const hs = document.getElementById('histogram_sync_with_operations');
                    if (hs) hs.value = config.manual.histogram_sync_with_operations === false ? 'false' : 'true';
                }
            }).catch(console.error);
        }

        function filterOps() {
            const search = document.getElementById('search').value.toLowerCase();
            const type = document.getElementById('typeFilter').value;
            let filtered = operations.filter(op => {
                const hay = ((op.desc || '') + ' ' + (op.subtitle || '') + ' ' + (op.bank || '') + ' ' + (op.requisite_phone || op.phone || '') + ' ' + (op.requisite_sender_name || op.sender_name || '') + ' ' + (op.card_number || '')).toLowerCase();
                if (search && !hay.includes(search)) return false;
                if (type === 'income' && op.type !== 'Credit') return false;
                if (type === 'expense' && op.type !== 'Debit') return false;
                return true;
            });
            displayOps(filtered);
        }

        function displayOps(ops) {
            if (!ops.length) {
                document.getElementById('operationsList').innerHTML = '<div style="padding:40px;text-align:center;">Нет операций</div>';
                return;
            }
            let html = '';
            ops.sort((a,b) => (Number(b.sort_ts||0) - Number(a.sort_ts||0))).forEach(op => {
                const hidden = hiddenOps.has(op.id);
                const income = op.type === 'Credit';
                const amount = Math.abs(op.amount||0);
                const sign = income ? '+' : '-';
                const metaLines = [];
                if (op.subtitle) metaLines.push(`<div class="op-meta-line">${escapeHtml(op.subtitle)}</div>`);
                if (op.requisite_phone || op.phone) metaLines.push(`<div class="op-meta-line requisite">${escapeHtml(op.requisite_phone || op.phone)}</div>`);
                if (op.requisite_sender_name || op.sender_name) metaLines.push(`<div class="op-meta-line requisite">${escapeHtml(op.requisite_sender_name || op.sender_name)}</div>`);
                if (op.card_number) metaLines.push(`<div class="op-meta-line requisite">${escapeHtml(op.card_number)}</div>`);
                if (op.bank) metaLines.push(`<div class="op-meta-line bank">${escapeHtml(op.bank)}${op.bank_preset && op.bank_preset !== 'custom' ? ' · ' + escapeHtml(op.bank_preset) : ''}</div>`);
                else if (op.bank_preset && op.bank_preset !== 'custom') metaLines.push(`<div class="op-meta-line bank">${escapeHtml(op.bank_preset)}</div>`);
                const opJson = JSON.stringify(op).replace(/</g, '\\u003c');
                const editBtn = op.manual ? `<button class="action-btn" style="margin-right:6px;" onclick='openEditManualModal(${opJson})' title="Изменить"><i class="fas fa-pen"></i></button>` : '';
                const canDelete = op.manual || op.fake_transfer;
                const delBtn = canDelete ? `<button class="action-btn" style="margin-right:6px;" onclick="deleteManualOp('${op.id}')" title="Удалить"><i class="fas fa-trash-alt"></i></button>` : '';
                const opTag = op.manual ? '<span style="color:#9f7aea;">· вручную</span>' : (op.fake_transfer ? '<span style="color:#63b3ed;">· перевод (мок)</span>' : '');
                html += `<div class="operation ${hidden?'hidden':''}"><div class="op-info"><div class="op-date"><i class="far fa-calendar-alt"></i> ${escapeHtml(op.date||'')}${opTag ? ' ' + opTag : ''}</div><div class="op-desc">${escapeHtml(op.desc||'—')}</div>${metaLines.join('')}</div><div style="display:flex;align-items:center;">${editBtn}${delBtn}<span class="op-amount ${income?'income':'expense'}">${sign} ${amount.toLocaleString()} ₽</span><button class="action-btn ${hidden?'btn-show':'btn-hide'}" onclick="toggleOp('${op.id}')"><i class="fas ${hidden?'fa-eye':'fa-eye-slash'}"></i></button></div></div>`;
            });
            document.getElementById('operationsList').innerHTML = html;
        }

        function escapeHtml(text) {
            if (!text) return '';
            return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }

        function toggleOp(id) {
            fetch('/api/toggle', { method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'id='+encodeURIComponent(id) })
                .then(() => loadOperations());
        }

        function showAllOps() {
            fetch('/api/show_all_operations', { method: 'POST' })
                .then(() => { showToast('Показаны все'); return loadOperations(); })
                .catch(() => showToast('Ошибка'));
        }

        function hideAllOps() {
            Promise.all(operations.filter(op => !hiddenOps.has(op.id)).map(op =>
                fetch('/api/toggle', { method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'id='+encodeURIComponent(op.id) })
            )).then(() => loadOperations());
        }

        function hideEntireHistoryOps() {
            if (!confirm('Скрыть все операции из кэша прокси (включая не отображённые в списке за месяц)?')) return;
            fetch('/api/hide_all_operations', { method: 'POST' })
                .then(r => r.json())
                .then(() => {
                    showToast('Вся история скрыта');
                    loadOperations();
                })
                .catch(() => showToast('Ошибка'));
        }

        function randomFillAndSave() {
            fetch('/api/random')
                .then(r => r.json())
                .then(data => {
                    if (data.name) {
                        document.getElementById('name_last').value = data.name.last_name || '';
                        document.getElementById('name_first').value = data.name.first_name || '';
                        document.getElementById('name_middle').value = data.name.middle_name || '';
                        document.getElementById('name_full').value = data.name.full_name || '';
                        document.getElementById('name_last_en').value = data.name.last_name_en || '';
                        document.getElementById('name_first_en').value = data.name.first_name_en || '';
                        document.getElementById('name_middle_en').value = data.name.middle_name_en || '';
                        document.getElementById('name_phone').value = data.name.phone || '';
                        document.getElementById('name_gender').value = data.name.gender || 'male';
                        document.getElementById('name_sex_code').value = data.name.sex_code || 'male';
                        document.getElementById('passport_series').value = data.name.passport_series || '';
                        document.getElementById('passport_number').value = data.name.passport_number || '';
                        document.getElementById('passport_issued').value = data.name.passport_issued_by || '';
                        document.getElementById('passport_date').value = data.name.passport_issue_date || '';
                        document.getElementById('inn').value = data.name.inn || '';
                    }
                    if (data.reki) {
                        document.getElementById('reki_contract').value = data.reki.contract || '';
                        document.getElementById('reki_account').value = data.reki.account || '';
                        document.getElementById('reki_recipient').value = data.reki.recipient || '';
                        document.getElementById('reki_beneficiary').value = data.reki.beneficiary || '';
                    }
                    if (data.balance) {
                        document.getElementById('balance_card').value = data.balance.new_card_number || '';
                        document.getElementById('balance_card2').value = data.balance.new_card_number2 || '';
                        document.getElementById('balance_amount').value = data.balance.new_balance || '';
                        document.getElementById('balance_collect').value = data.balance.new_collect_sum || '';
                    }
                    fetch('/api/config/save', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(data)
                    }).catch(err => console.warn('save random error', err));
                })
                .catch(console.error);
        }

        function saveRequisites() {
            saveConfig({
                reki: {
                    contract: document.getElementById('reki_contract').value,
                    account: document.getElementById('reki_account').value,
                    recipient: document.getElementById('reki_recipient').value,
                    beneficiary: document.getElementById('reki_beneficiary').value
                },
                balance: {
                    new_card_number: document.getElementById('balance_card').value,
                    new_card_number2: document.getElementById('balance_card2').value
                }
            });
        }

        function saveName() {
            saveConfig({ name: {
                last_name: document.getElementById('name_last').value,
                first_name: document.getElementById('name_first').value,
                middle_name: document.getElementById('name_middle').value,
                full_name: document.getElementById('name_full').value,
                last_name_en: document.getElementById('name_last_en').value,
                first_name_en: document.getElementById('name_first_en').value,
                middle_name_en: document.getElementById('name_middle_en').value,
                phone: document.getElementById('name_phone').value,
                email: document.getElementById('name_email').value,
                gender: document.getElementById('name_gender').value,
                sex_code: document.getElementById('name_sex_code').value
            }});
        }

        function addManualOperation() {
            const amount = parseFloat(document.getElementById('manual_amount').value) || 0;
            if (amount <= 0) { showToast('Укажите сумму'); return; }
            const recipientType = document.getElementById('manual_recipient_type').value;
            const direction = document.getElementById('manual_direction').value;
            const requisitePhone = (document.getElementById('manual_phone').value || '').trim();
            const requisiteSenderName = (document.getElementById('manual_sender_name').value || '').trim();
            const body = {
                direction: direction,
                amount: amount,
                bank: document.getElementById('manual_bank').value,
                description: document.getElementById('manual_desc').value,
                title: document.getElementById('manual_title').value,
                subtitle: document.getElementById('manual_subtitle').value,
                bank_preset: document.getElementById('manual_bank_preset').value,
                datetime: document.getElementById('manual_datetime').value || null,
                phone: requisitePhone,
                requisite_phone: requisitePhone,
                card_number: direction === 'out' && recipientType === 'card' ? document.getElementById('manual_card_number').value : '',
                sender_name: requisiteSenderName,
                requisite_sender_name: requisiteSenderName,
                receipt_phone: direction === 'in' ? (document.getElementById('manual_receipt_phone').value || '').trim() : ''
            };
            fetch('/api/operations/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            })
            .then(r => r.json())
            .then(d => {
                if (d.error) throw new Error(d.error);
                showToast('Добавлена новая операция');
                document.getElementById('manual_amount').value = '';
                document.getElementById('manual_title').value = '';
                document.getElementById('manual_subtitle').value = '';
                document.getElementById('manual_phone').value = '';
                document.getElementById('manual_card_number').value = '';
                document.getElementById('manual_sender_name').value = '';
                const mr = document.getElementById('manual_receipt_phone');
                if (mr) mr.value = '';
                document.getElementById('manual_desc').value = '';
                loadOperations();
            })
            .catch(() => showToast('Ошибка добавления'));
        }

        function deleteManualOp(id) {
            if (!confirm('Удалить эту ручную операцию?')) return;
            fetch('/api/operations/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: id })
            })
            .then(r => r.json())
            .then(d => {
                if (d.error) throw new Error(d.error);
                showToast('Удалено');
                loadOperations();
            })
            .catch(() => showToast('Ошибка'));
        }

        function saveDocuments() {
            saveConfig({ name: {
                passport_series: document.getElementById('passport_series').value,
                passport_number: document.getElementById('passport_number').value,
                passport_issued_by: document.getElementById('passport_issued').value,
                passport_issue_date: document.getElementById('passport_date').value,
                inn: document.getElementById('inn').value
            }});
        }

        function saveBalance() {
            saveConfig({ balance: {
                new_balance: parseFloat(document.getElementById('balance_amount').value) || 0,
                new_collect_sum: parseInt(document.getElementById('balance_collect').value) || 0
            }});
        }

        function saveSettings() {
            saveConfig({
                history: {
                    show_categories: document.getElementById('show_categories').value === 'true',
                    sort_direction: document.getElementById('sort_direction').value,
                    panel_show_all_operations: document.getElementById('panel_show_all_operations').value === 'true'
                },
                manual: {
                    panel_sync_bank_histogram: document.getElementById('panel_sync_bank_histogram').value === 'true',
                    histogram_sync_with_operations: document.getElementById('histogram_sync_with_operations').value === 'true'
                }
            });
        }

        function saveConfig(newConfig) {
            fetch('/api/config/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(newConfig)
            })
            .then(r => r.json())
            .then(() => showToast('✅ Сохранено'))
            .catch(() => showToast('❌ Ошибка'));
        }

        document.getElementById('search').addEventListener('keyup', filterOps);
        document.getElementById('typeFilter').addEventListener('change', filterOps);
        document.getElementById('manual_bank_preset').addEventListener('change', () => { renderPresetLogos(); syncRecipientTypeWithBankPreset('manual'); });
        document.getElementById('manual_direction').addEventListener('change', () => updateDirectionSpecificFields('manual'));
        document.getElementById('manual_recipient_type').addEventListener('change', () => updateRecipientFields('manual'));
        document.getElementById('edit_bank_preset').addEventListener('change', () => syncRecipientTypeWithBankPreset('edit'));
        document.getElementById('edit_direction').addEventListener('change', () => updateDirectionSpecificFields('edit'));
        document.getElementById('edit_recipient_type').addEventListener('change', () => updateRecipientFields('edit'));

        loadAllData();
        syncRecipientTypeWithBankPreset('manual');
        syncRecipientTypeWithBankPreset('edit');
        updateDirectionSpecificFields('manual');
        updateDirectionSpecificFields('edit');
        setInterval(loadOperations, 3200);
    </script>
</body>
</html>"""

def request(flow: http.HTTPFlow) -> None:
    # Сначала порт: иначе 403 на весь HTTPS банка для «чужих» IP (телефон в LAN и т.д.).
    if flow.request.port != PANEL_PORT:
        return

    client_ip = flow.client_conn.address[0]
    if not _client_ok_for_panel(client_ip):
        flow.response = http.Response.make(403, b"Forbidden")
        return

    path = flow.request.path
    path_only = _panel_request_path_only(path)

    cors_pdf = {
        "Content-Type": "application/pdf",
        "Access-Control-Allow-Origin": "*",
        "Content-Disposition": 'inline; filename="receipt.pdf"',
    }

    if flow.request.method == "OPTIONS" and path_only == "/api/manual_operation_receipt":
        flow.response = http.Response.make(
            204,
            b"",
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
        return

    if flow.request.method == "GET" and path_only == "/api/manual_operation_receipt":
        qs = _panel_request_query_params(path)
        op_id = (qs.get("operationId") or qs.get("operation_id") or qs.get("id") or [""])[0]
        op_id = (op_id or "").strip()
        history.ensure_manual_operations_fresh()
        pdf_abs = history.ensure_operation_receipt_pdf_path(op_id)
        if not pdf_abs or not os.path.isfile(pdf_abs):
            flow.response = http.Response.make(
                404,
                json.dumps({"error": "operation not found"}, ensure_ascii=False).encode("utf-8"),
                {"Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*"},
            )
            return
        try:
            data = Path(pdf_abs).read_bytes()
        except OSError:
            flow.response = http.Response.make(500, b"read error")
            return
        h = dict(cors_pdf)
        h["Content-Length"] = str(len(data))
        flow.response = http.Response.make(200, data, h)
        return

    if flow.request.method == "OPTIONS" and path == "/api/effective_balance":
        flow.response = http.Response.make(
            204,
            b"",
            {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )
        return

    if path == "/admin" or path == "/admin/":
        flow.response = http.Response.make(200, HTML_PANEL.encode('utf-8'), {"Content-Type": "text/html"})
        return

    if path == "/api/config":
        flow.response = http.Response.make(200, json.dumps(controller.config, ensure_ascii=False).encode('utf-8'), {"Content-Type": "application/json"})
        return

    if path == "/api/effective_balance":
        body = json.dumps({"value": effective_balance_value()}, ensure_ascii=False).encode("utf-8")
        flow.response = http.Response.make(
            200,
            body,
            {
                "Content-Type": "application/json; charset=utf-8",
                "Access-Control-Allow-Origin": "*",
            },
        )
        return

    if path == "/api/random":
        flow.response = http.Response.make(200, json.dumps(random_data(), ensure_ascii=False).encode('utf-8'), {"Content-Type": "application/json"})
        return

    if path == "/api/set_manual_stats":
        try:
            data = json.loads(flow.request.text)
            if (
                'income' in data
                or 'expense' in data
                or 'histogram_sync_with_operations' in data
                or 'panel_sync_bank_histogram' in data
            ):
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
                if 'panel_sync_bank_histogram' in data:
                    v = data['panel_sync_bank_histogram']
                    if v is None:
                        controller.config['manual'].pop('panel_sync_bank_histogram', None)
                    else:
                        controller.config['manual']['panel_sync_bank_histogram'] = bool(v)
                controller.save_config()
                flow.response = http.Response.make(200, json.dumps({"status": "ok"}).encode('utf-8'), {"Content-Type": "application/json"})
            else:
                flow.response = http.Response.make(400, b'{"error":"bad request"}')
        except Exception:
            flow.response = http.Response.make(400, b'{"error":"bad request"}')
        return

    if flow.request.method == "POST" and path == "/api/config/save":
        try:
            new_config = json.loads(flow.request.text)
            for key, value in new_config.items():
                if key in controller.config and isinstance(controller.config[key], dict) and isinstance(value, dict):
                    controller.config[key].update(value)
                else:
                    controller.config[key] = value
            controller.save_config()
            flow.response = http.Response.make(200, json.dumps({"status": "ok"}).encode('utf-8'), {"Content-Type": "application/json"})
        except Exception:
            flow.response = http.Response.make(400, b'{"error":"bad request"}')
        return

    if path == "/favicon.ico":
        flow.response = http.Response.make(204)

EMAIL_JSON_KEYS = frozenset({
    "email", "userEmail", "emailAddress", "contactEmail", "maskedEmail",
    "mail", "primaryEmail", "loginEmail", "recoveryEmail", "unconfirmedEmail"
})

def substitute_email_in_obj(obj, new_email: str) -> None:
    if not new_email:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in EMAIL_JSON_KEYS and isinstance(v, str) and "@" in v:
                obj[k] = new_email
            elif k == "login" and isinstance(v, str) and "@" in v:
                obj[k] = new_email
            else:
                substitute_email_in_obj(v, new_email)
    elif isinstance(obj, list):
        for item in obj:
            substitute_email_in_obj(item, new_email)

def response(flow: http.HTTPFlow) -> None:
    url = flow.request.pretty_url
    if not is_bank_flow(flow):
        return
    if not flow.response:
        return
    ensure_response_decoded(flow)
    if not flow.response.text:
        return
    if not is_jsonish_response(flow):
        return

    if url_prohibit_proxy_json_mutation(url):
        return

    if flow_statements_spravki_context(flow):
        return

    # Подмена гистограмм/сводок расходов-доходов. На mybank URL у Т-Банка периодически меняются,
    # поэтому ориентируемся не только на путь, но и на саму структуру ответа.
    ul = url.lower()
    body = flow.response.text or ""
    histogram_like = (
        "operations_histogram" in ul
        or "operations_category_list" in ul
        or (
            "graphql" in ul
            and '"spending"' in body
            and '"summary"' in body
        )
        or (
            '"earning"' in body
            and '"spending"' in body
            and '"summary"' in body
        )
    )
    if histogram_like:
        try:
            data = json.loads(flow.response.text)
            history.record_bank_histogram_from_payload(data)
            display_income, display_expense, _, _ = history.get_panel_chart_display_totals()

            def replace_histogram_amounts(obj, income, expense):
                if isinstance(obj, dict):
                    if "earning" in obj and "summary" in obj["earning"]:
                        if "value" in obj["earning"]["summary"]:
                            old_income = obj["earning"]["summary"]["value"]
                            obj["earning"]["summary"]["value"] = income
                            if "intervals" in obj["earning"] and old_income > 0 and income > 0:
                                for interval in obj["earning"]["intervals"]:
                                    if "aggregated" in interval:
                                        for cat in interval["aggregated"]:
                                            if "amount" in cat and "value" in cat["amount"]:
                                                cat["amount"]["value"] = round(cat["amount"]["value"] * income / old_income, 2)
                    if "spending" in obj and "summary" in obj["spending"]:
                        if "value" in obj["spending"]["summary"]:
                            old_expense = obj["spending"]["summary"]["value"]
                            obj["spending"]["summary"]["value"] = expense
                            if "intervals" in obj["spending"] and old_expense > 0 and expense > 0:
                                for interval in obj["spending"]["intervals"]:
                                    if "aggregated" in interval:
                                        for cat in interval["aggregated"]:
                                            if "amount" in cat and "value" in cat["amount"]:
                                                cat["amount"]["value"] = round(cat["amount"]["value"] * expense / old_expense, 2)
                    for key, val in obj.items():
                        replace_histogram_amounts(val, income, expense)
                elif isinstance(obj, list):
                    for item in obj:
                        replace_histogram_amounts(item, income, expense)

            replace_histogram_amounts(data, display_income, display_expense)
            flow.response.text = json.dumps(data, ensure_ascii=False)
            print(f"[panel] Подменены гистограммы для {url} (доходы: {display_income}, расходы: {display_expense})")
        except Exception as e:
            print(f"[panel] Ошибка подмены гистограммы: {e}")

    # Подмена карт
    if any(keyword in url for keyword in ["cards", "account_cards", "card_credentials"]):
        try:
            data = json.loads(flow.response.text)
            card1 = controller.config.get('balance', {}).get('new_card_number', '')
            card2 = controller.config.get('balance', {}).get('new_card_number2', '')
            if not card1 and not card2:
                return

            card_counter = 0
            def replace_cards(obj):
                nonlocal card_counter
                if isinstance(obj, dict):
                    for field in ['pan', 'cardNumber', 'number', 'maskedPan']:
                        if field in obj:
                            if card_counter == 0 and card1:
                                obj[field] = card1
                                card_counter += 1
                            elif card_counter == 1 and card2:
                                obj[field] = card2
                                card_counter += 1
                    for key, val in obj.items():
                        replace_cards(val)
                elif isinstance(obj, list):
                    for item in obj:
                        replace_cards(item)

            replace_cards(data)
            flow.response.text = json.dumps(data, ensure_ascii=False)
            print(f"[panel] Подменены номера карт в ответе для {url}")
        except Exception as e:
            print(f"[panel] Ошибка подмены карт: {e}")

    replacement = (controller.config.get("name") or {}).get("email") or ""
    replacement = str(replacement).strip()
    if replacement:
        try:
            data = json.loads(flow.response.text)
            substitute_email_in_obj(data, replacement)
            flow.response.text = json.dumps(data, ensure_ascii=False)
        except Exception as e:
            print(f"[panel] Подмена email: {e}")

print(f"[+] panel_bridge загружен, панель доступна по http://{HOST_IP}:{PANEL_PORT}/admin")