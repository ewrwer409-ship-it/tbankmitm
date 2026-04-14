#!/usr/bin/env bash
# Один раз на Ubuntu VPS: venv, mitmproxy, PyMuPDF
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Нужен Python 3. Установите: sudo apt update && sudo apt install -y python3 python3-venv"
  exit 1
fi

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install mitmproxy pymupdf
./venv/bin/python -c "import mitmproxy, fitz; print('OK: mitmproxy и PyMuPDF')"
echo
echo "Готово. Запуск: ./start_vps.sh"
echo "Откройте порт 8082 в firewall (sudo ufw allow 8082/tcp && sudo ufw enable)."
