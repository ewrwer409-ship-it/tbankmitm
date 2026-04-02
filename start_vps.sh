#!/usr/bin/env bash
# Прокси и панель на всех интерфейсах :8082 (iPhone + Potatso).
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x venv/bin/python ]]; then
  echo "Сначала: chmod +x setup_linux.sh start_vps.sh && ./setup_linux.sh"
  exit 1
fi

# Панель с телефона в мобильной сети: без этого будет 403 из-за фильтра IP в panel_bridge.
export TBANKMITM_PANEL_ALLOW_ANY=1

# Для сообщения при загрузке скрипта; подставьте свой публичный IP VPS.
if [[ -z "${TBANKMITM_PUBLIC_IP:-}" ]]; then
  TBANKMITM_PUBLIC_IP="$(curl -4 -s --connect-timeout 3 ifconfig.me 2>/dev/null || true)"
fi
export TBANKMITM_PUBLIC_IP

exec ./venv/bin/python mitm_run_dump.py \
  -s transfer.py \
  -s controller.py \
  -s balance.py \
  -s history.py \
  -s operation_detail.py \
  -s name.py \
  -s reki.py \
  -s panel_bridge.py \
  -s browser_ops_injector.py \
  -s tbank_sbp_debit_injector.py \
  --listen-host 0.0.0.0 \
  -p 8082 \
  --set block_global=false \
  --set ssl_insecure=true \
  --set http2=false
