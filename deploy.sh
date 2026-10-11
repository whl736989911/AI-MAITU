#!/usr/bin/env bash
# Local deployment entry: sudo bash deploy.sh center|platform|check.
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '需要 Python 3。Ubuntu 可先运行：sudo apt-get install python3' >&2
  exit 1
fi
# Isolate imports from inherited PYTHONPATH and user site-packages when running as root.
exec python3 -I "$ROOT/scripts/deploy.py" "$@"
