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

read -r -s "BRAVE_KEY?请输入 Brave Search API Key（输入内容不会显示）: "
printf "\n"
if [[ -z "$BRAVE_KEY" || "$BRAVE_KEY" =~ '[[:space:]]' ]]; then
  echo "API Key 格式无效，未修改本地配置。" >&2
  exit 64
fi

echo "正在调用 Brave Search 验证 API Key（会消耗 1 次请求）..."
(
  cd "$ROOT"
  BRAVE_SEARCH_API_KEY="$BRAVE_KEY" "$PYTHON_BIN" - <<'PY'
import os

from app import brave_web_search

results = brave_web_search(
    os.environ["BRAVE_SEARCH_API_KEY"],
    "山东 水处理 企业 官网",
    3,
)
print(f"Brave Search 验证成功，测试返回 {len(results)} 条网页结果。")
PY
)

temp_file="$(mktemp "$ENV_DIR/local.env.XXXXXX")"
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == BRAVE_SEARCH_API_KEY=* ]]; then
    if (( found == 0 )); then
      printf 'BRAVE_SEARCH_API_KEY=%s\n' "$BRAVE_KEY" >>"$temp_file"
      found=1
    fi
  else
    printf '%s\n' "$line" >>"$temp_file"
  fi
done <"$ENV_FILE"
if (( found == 0 )); then
  printf 'BRAVE_SEARCH_API_KEY=%s\n' "$BRAVE_KEY" >>"$temp_file"
fi
chmod 600 "$temp_file"
mv "$temp_file" "$ENV_FILE"
unset BRAVE_KEY

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
