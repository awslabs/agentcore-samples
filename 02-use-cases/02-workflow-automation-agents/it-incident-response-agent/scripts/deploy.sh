#!/usr/bin/env bash
# Fresh-only deployment. Explicit --project --target --account --region required.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/lifecycle.py" deploy "$@"
