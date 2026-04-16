#!/usr/bin/env bash
# Один раз на Ubuntu VPS: venv, mitmproxy, PyMuPDF, проверка шаблона выписки (Выписка.pdf).
#
# Опционально системные пакеты (curl для start_vps, шрифты для PDF без TinkoffSans.ttf,
# ghostscript для сжатия чеков в func.py):
#   TBANKMITM_APT=1 ./setup_linux.sh
#
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3. Установите: sudo apt update && sudo apt install -y python3 python3-venv"
  exit 1
fi

if [[ "${TBANKMITM_APT:-}" == "1" ]] && command -v apt-get >/dev/null 2>&1; then
  echo "[setup] TBANKMITM_APT=1 — ставлю curl, шрифты DejaVu, ghostscript…"
  if [[ "$(id -u)" -eq 0 ]]; then
    apt-get update -qq
    apt-get install -y --no-install-recommends curl fonts-dejavu-core ghostscript
  else
    sudo apt-get update -qq
    sudo apt-get install -y --no-install-recommends curl fonts-dejavu-core ghostscript
  fi
else
  echo "Подсказка: на VPS удобно выполнить TBANKMITM_APT=1 $0 — поставятся curl, fonts-dejavu-core, ghostscript."
fi

if [[ -d venv ]]; then
  echo "venv уже есть — обновляю pip и пакеты (для чистой установки удалите: rm -rf venv)."
else
  python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip
./venv/bin/pip install mitmproxy pymupdf

./venv/bin/python -c "import mitmproxy, fitz; print('OK: mitmproxy и PyMuPDF')"

./venv/bin/python <<'PYCHECK'
import os
import sys

try:
    import statement_template_fill as st
except Exception as ex:
    print("WARN: statement_template_fill:", ex)
    sys.exit(0)

p = st.template_pdf_path()
if os.path.isfile(p):
    print("OK: шаблон выписки:", p)
else:
    print("WARN: нет файла шаблона выписки — положите Выписка.pdf рядом со скриптами:", p)
PYCHECK

echo
echo "Готово. Запуск прокси и панели: ./start_vps.sh"
echo "Откройте порт 8082 в firewall (sudo ufw allow 8082/tcp && sudo ufw enable)."
