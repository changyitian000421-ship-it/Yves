#!/bin/zsh
set -euo pipefail

ROOT="/Users/yves/Documents/New project"
ENV_FILE="$ROOT/data/local.env"
APP_SUPPORT_ENV="/Users/yves/Library/Application Support/CalciumLeads/local.env"
LOG_DIR="$ROOT/data/logs"
PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"

mkdir -p "$ROOT/data" "$LOG_DIR" "$ROOT/data/backups"
cd "$ROOT"

if [[ -f "$APP_SUPPORT_ENV" ]]; then
  set -a
  source "$APP_SUPPORT_ENV"
  set +a
elif [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

export DATA_DIR="${DATA_DIR:-$ROOT/data}"
export AMAP_WORKERS="${AMAP_WORKERS:-4}"

if [[ -z "${APP_PASSWORD:-}" || -z "${LOGIN_PHONES:-}" ]]; then
  echo "APP_PASSWORD or LOGIN_PHONES missing. Create $ENV_FILE first." >&2
  exit 78
fi

exec "$PYTHON_BIN" "$ROOT/app.py" --host 127.0.0.1 --port 8765
