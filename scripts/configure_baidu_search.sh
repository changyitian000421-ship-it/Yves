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

read -r -s "BAIDU_SEARCH_KEY?请输入百度千帆 API Key（输入内容不会显示）: "
printf "\n"
if [[ -z "$BAIDU_SEARCH_KEY" || "$BAIDU_SEARCH_KEY" =~ '[[:space:]]' ]]; then
  echo "API Key 格式无效，未修改本地配置。" >&2
  exit 64
fi

echo "正在调用百度千帆网页搜索验证 API Key..."
(
  cd "$ROOT"
  BAIDU_SEARCH_API_KEY="$BAIDU_SEARCH_KEY" "$PYTHON_BIN" - <<'PY'
import os

from app import qianfan_web_search

results = qianfan_web_search(
    os.environ["BAIDU_SEARCH_API_KEY"],
    "山东 水处理 企业 官网",
    5,
)
print(f"百度千帆验证成功，测试返回 {len(results)} 条网页结果。")
PY
)

temp_file="$(mktemp "$ENV_DIR/local.env.XXXXXX")"
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == BAIDU_SEARCH_API_KEY=* ]]; then
    if (( found == 0 )); then
      printf 'BAIDU_SEARCH_API_KEY=%s\n' "$BAIDU_SEARCH_KEY" >>"$temp_file"
      found=1
    fi
  else
    printf '%s\n' "$line" >>"$temp_file"
  fi
done <"$ENV_FILE"
if (( found == 0 )); then
  printf 'BAIDU_SEARCH_API_KEY=%s\n' "$BAIDU_SEARCH_KEY" >>"$temp_file"
fi
chmod 600 "$temp_file"
mv "$temp_file" "$ENV_FILE"
unset BAIDU_SEARCH_KEY

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
