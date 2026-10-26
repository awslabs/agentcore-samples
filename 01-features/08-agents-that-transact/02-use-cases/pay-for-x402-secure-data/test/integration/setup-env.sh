#!/usr/bin/env bash
# Seed .env from env-sample.txt and generate a fresh USER_ID. Idempotent:
# re-runs leave existing values alone.
#
# Usage:
#   bash test/integration/setup-env.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/setup_env.py"
