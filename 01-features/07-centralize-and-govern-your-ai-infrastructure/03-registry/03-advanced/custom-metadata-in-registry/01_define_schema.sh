#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"; [ -d .venv ] || { echo "Run ./setup.sh first" >&2; exit 1; }
source .venv/bin/activate && python -u steps/01_define_schema.py "$@"
