#!/usr/bin/env bash
# Delete only the recorded owned StackId; retain state/ledger on every outcome.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/lifecycle.py" destroy "$@"
