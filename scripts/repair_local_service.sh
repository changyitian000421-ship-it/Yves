#!/bin/zsh
set -euo pipefail

ROOT="/Users/yves/Documents/New project"
LABEL="com.yves.calciumleads"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
URL="http://127.0.0.1:8765/health"

health_ok() {
  curl -fsS --max-time 3 "$URL" >/dev/null 2>&1
}

if health_ok; then
  echo "OK: local website is running at http://127.0.0.1:8765/"
  exit 0
fi

echo "Local website is not healthy. Repairing launch service..."
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true

for pid in $(lsof -t -iTCP:8765 -sTCP:LISTEN 2>/dev/null || true); do
  command=$(ps -p "$pid" -o command= 2>/dev/null || true)
  if [[ "$command" == *"$ROOT/app.py"* ]]; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
done

launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl enable "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true

for _ in {1..20}; do
  if health_ok; then
    echo "OK: local website repaired at http://127.0.0.1:8765/"
    exit 0
  fi
  sleep 0.5
done

echo "FAILED: local website is still not healthy. Recent logs:" >&2
tail -80 "$ROOT/data/logs/launchd.err.log" 2>/dev/null >&2 || true
tail -80 "$ROOT/data/logs/launchd.out.log" 2>/dev/null >&2 || true
exit 1
