#!/bin/zsh
set -euo pipefail

ROOT="/Users/yves/Documents/New project"
ENV_DIR="$HOME/Library/Application Support/CalciumLeads"
ENV_FILE="$ENV_DIR/local.env"
PYTHON_BIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
LABEL="com.yves.calciumleads"

mkdir -p "$ENV_DIR"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

read -r -s "HUNTER_KEY?请输入 Hunter API Key（输入内容不会显示）: "
printf "\n"
if [[ -z "$HUNTER_KEY" || "$HUNTER_KEY" =~ '[[:space:]]' ]]; then
  echo "API Key 格式无效，未修改本地配置。" >&2
  exit 64
fi

echo "正在验证 Hunter API Key（账户验证不执行企业邮箱查询）..."
(
  cd "$ROOT"
  HUNTER_API_KEY="$HUNTER_KEY" "$PYTHON_BIN" - <<'PY'
import json
import os
from urllib.request import Request

from app import read_url_bytes

request = Request(
    "https://api.hunter.io/v2/account",
    headers={
        "Accept": "application/json",
        "User-Agent": "CalciumLeads/1.0",
        "X-API-KEY": os.environ["HUNTER_API_KEY"],
    },
)
payload = json.loads(read_url_bytes(request, timeout=20).decode("utf-8"))
if not isinstance(payload.get("data"), dict):
    raise SystemExit("Hunter API Key 验证失败。")
print("Hunter API Key 验证成功。")
PY
)

temp_file="$(mktemp "$ENV_DIR/local.env.XXXXXX")"
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == HUNTER_API_KEY=* ]]; then
    if (( found == 0 )); then
      printf 'HUNTER_API_KEY=%s\n' "$HUNTER_KEY" >>"$temp_file"
      found=1
    fi
  else
    printf '%s\n' "$line" >>"$temp_file"
  fi
done <"$ENV_FILE"
if (( found == 0 )); then
  printf 'HUNTER_API_KEY=%s\n' "$HUNTER_KEY" >>"$temp_file"
fi
chmod 600 "$temp_file"
mv "$temp_file" "$ENV_FILE"
unset HUNTER_KEY

launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
for _ in {1..30}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:8765/health" >/dev/null 2>&1; then
    echo "配置完成，本地网站已重启：http://127.0.0.1:8765/"
    exit 0
  fi
  sleep 0.5
done

echo "API Key 已保存，但本地网站未及时恢复，请运行 scripts/repair_local_service.sh。" >&2
exit 1
