#!/bin/zsh
set -euo pipefail

ROOT="/Users/yves/Documents/New project"
"$ROOT/scripts/repair_local_service.sh"
open "http://127.0.0.1:8765/"
