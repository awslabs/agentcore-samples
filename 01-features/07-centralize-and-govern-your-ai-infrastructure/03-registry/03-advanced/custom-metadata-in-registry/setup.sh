#!/usr/bin/env bash
# Run once: creates .venv with Python 3.11+ and installs requirements.txt.
set -euo pipefail
cd "$(dirname "$0")"
for py in python3.13 python3.12 python3.11 python3; do
  command -v "$py" >/dev/null && "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' && break
  py=""
done
[ -n "$py" ] || { echo "Python 3.11 or later is required" >&2; exit 1; }
"$py" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip -r requirements.txt
.venv/bin/python -c 'import boto3, strands, mcp_proxy_for_aws; print("boto3", boto3.__version__)'
