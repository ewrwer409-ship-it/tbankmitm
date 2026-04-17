#!/usr/bin/env bash
# Прокси и панель на всех интерфейсах :8082 (iPhone + Potatso).
# Цепочка mitm-скриптов = mitm_addon_chain.py (тот же набор, что start.bat через _proxy_cmd.bat).
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x venv/bin/python ]]; then
  echo "Сначала: chmod +x setup_linux.sh start_vps.sh && ./setup_linux.sh"
  echo "Полный стек (curl, шрифты PDF, ghostscript): TBANKMITM_APT=1 ./setup_linux.sh"
  exit 1
fi

# Та же проверка, что после git pull не остался старый bank_filter без t-bank-app (*.t-bank-app.ru).
./venv/bin/python -c "from bank_filter import _BANK_KEYS; assert 't-bank-app' in _BANK_KEYS, 'Обновите репозиторий: в bank_filter.py нужен ключ t-bank-app для Drive Transit / встраиваемого банка'"

# Кириллица в панели / описаниях операций / PDF на минимальных образах Debian/Ubuntu
export PYTHONUTF8="${PYTHONUTF8:-1}"
if [[ -z "${LC_ALL:-}" ]]; then
  export LC_ALL=C.UTF-8
fi
if [[ -z "${LANG:-}" ]]; then
  export LANG=C.UTF-8
fi

# Панель с телефона в мобильной сети: без этого будет 403 из-за фильтра IP в panel_bridge.
export TBANKMITM_PANEL_ALLOW_ANY=1

# Публичный IP VPS: сначала env, иначе curl; иначе этот сервер (fetch /mybank с телефона идёт на 127.0.0.1 без origin).
if [[ -z "${TBANKMITM_PUBLIC_IP:-}" ]]; then
  TBANKMITM_PUBLIC_IP="$(curl -4 -s --connect-timeout 3 ifconfig.me 2>/dev/null || true)"
fi
TBANKMITM_PUBLIC_IP="${TBANKMITM_PUBLIC_IP:-85.192.60.79}"
export TBANKMITM_PUBLIC_IP

export TBANKMITM_PROXY_LISTEN_HOST=0.0.0.0
export TBANKMITM_PROXY_PORT=8082

# С телефона WebView ходит не на 127.0.0.1 VPS — подставить origin API панели (если не задан в config.json panel_fetch_origin).
if [[ -z "${TBANK_PANEL_FETCH_ORIGIN:-}" ]]; then
  export TBANK_PANEL_FETCH_ORIGIN="http://${TBANKMITM_PUBLIC_IP}:${TBANKMITM_PROXY_PORT}"
fi

exec ./venv/bin/python mitm_run_dump.py
