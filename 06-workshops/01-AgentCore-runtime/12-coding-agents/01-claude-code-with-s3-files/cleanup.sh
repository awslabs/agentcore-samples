#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "  AgentCore Cleanup"
echo "============================================================"
echo ""

python3 cleanup.py

echo ""
echo "Done."
