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

read -r -s "BAIDU_AK?请输入百度地图服务端 AK（输入内容不会显示）: "
printf "\n"
if [[ -z "$BAIDU_AK" || ! "$BAIDU_AK" =~ '^[A-Za-z0-9_-]+$' ]]; then
  echo "AK 格式无效，未修改本地配置。" >&2
  exit 64
fi

echo "正在调用百度地点检索 3.0 验证 AK..."
(
  cd "$ROOT"
  BAIDU_MAP_AK="$BAIDU_AK" "$PYTHON_BIN" - <<'PY'
import os

from app import baidu_map_search

result = baidu_map_search(
    os.environ["BAIDU_MAP_AK"],
    "济南市",
    "水处理公司",
    1,
)
print(f"百度地图验证成功，测试返回 {len(result.get('results') or [])} 条地点。")
PY
)

temp_file="$(mktemp "$ENV_DIR/local.env.XXXXXX")"
found=0
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == BAIDU_MAP_AK=* ]]; then
    if (( found == 0 )); then
      printf 'BAIDU_MAP_AK=%s\n' "$BAIDU_AK" >>"$temp_file"
      found=1
    fi
  else
    printf '%s\n' "$line" >>"$temp_file"
  fi
done <"$ENV_FILE"
if (( found == 0 )); then
  printf 'BAIDU_MAP_AK=%s\n' "$BAIDU_AK" >>"$temp_file"
fi
chmod 600 "$temp_file"
mv "$temp_file" "$ENV_FILE"
unset BAIDU_AK

launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
for _ in {1..30}; do
  if curl -fsS --max-time 2 "http://127.0.0.1:8765/health" >/dev/null 2>&1; then
    echo "配置完成，本地网站已重启：http://127.0.0.1:8765/"
    exit 0
  fi
  sleep 0.5
done

echo "AK 已保存，但本地网站未及时恢复，请运行 scripts/repair_local_service.sh。" >&2
exit 1
