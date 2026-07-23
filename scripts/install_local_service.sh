#!/bin/zsh
set -euo pipefail

ROOT="/Users/yves/Documents/New project"
LABEL="com.yves.calciumleads"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$ROOT/data/logs"
APP_SUPPORT_DIR="$HOME/Library/Application Support/CalciumLeads"
APP_SUPPORT_ENV="$APP_SUPPORT_DIR/local.env"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR" "$ROOT/data" "$APP_SUPPORT_DIR"
chmod +x "$ROOT/scripts/start_local_service.sh" "$ROOT/scripts/repair_local_service.sh"
if [[ -f "$ROOT/data/local.env" && ! -f "$APP_SUPPORT_ENV" ]]; then
  cp "$ROOT/data/local.env" "$APP_SUPPORT_ENV"
  chmod 600 "$APP_SUPPORT_ENV"
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string><![CDATA[
cd "$ROOT" || exit 70
set -a
[[ -f "$APP_SUPPORT_ENV" ]] && source "$APP_SUPPORT_ENV"
set +a
export DATA_DIR="\${DATA_DIR:-$ROOT/data}"
export AMAP_WORKERS="\${AMAP_WORKERS:-4}"
export BAIDU_MAP_WORKERS="\${BAIDU_MAP_WORKERS:-4}"
if [[ -z "\${APP_PASSWORD:-}" || -z "\${LOGIN_PHONES:-}" ]]; then
  echo "APP_PASSWORD or LOGIN_PHONES missing in $APP_SUPPORT_ENV" >&2
  exit 78
fi
exec /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 "$ROOT/app.py" --host 127.0.0.1 --port 8765
]]></string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.err.log</string>
  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
"$ROOT/scripts/repair_local_service.sh"
