#!/bin/bash
set -euo pipefail
# Auth must not be removed while a Runtime/Gateway is live.
exec python3 "$(dirname "$0")/lifecycle.py" cleanup "$@"
