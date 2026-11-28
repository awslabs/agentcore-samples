#!/usr/bin/env bash
# Publish synthetic input only to a verified deployment (--ticket path optional).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/lifecycle.py" publish "$@"
