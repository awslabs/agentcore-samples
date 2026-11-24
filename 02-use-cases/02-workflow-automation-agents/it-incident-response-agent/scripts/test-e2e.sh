#!/usr/bin/env bash
# Full correlated synthetic E2E; explicit project/target/account/region required.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/test_e2e.py" "$@"
