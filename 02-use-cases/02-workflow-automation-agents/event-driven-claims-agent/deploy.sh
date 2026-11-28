#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
# Explicit cloud opt-in; this never sources .env, bootstraps or seeds/sends mail.
exec python3 scripts/lifecycle.py deploy "$@"
