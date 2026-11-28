#!/usr/bin/env bash
# Inspect a synthetic ticket in the verified stack; explicit binding required.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/lifecycle.py" show "$@"
