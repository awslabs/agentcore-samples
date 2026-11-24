#!/bin/bash
set -euo pipefail
# Dedicated fresh pool/provider only. Never reuse a pool by name or store secrets.
exec python3 "$(dirname "$0")/lifecycle.py" setup "$@"
