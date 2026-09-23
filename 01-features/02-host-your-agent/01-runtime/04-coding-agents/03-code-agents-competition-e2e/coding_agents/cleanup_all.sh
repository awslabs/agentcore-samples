#!/usr/bin/env bash
# Delete all coding agent runtimes and their IAM roles.
# Does NOT delete shared infra (VPC, S3 Files) — use infra/cleanup.sh for that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

AGENTS=(claude-code kiro codex cursor hermes open-code)

echo "=============================================="
echo "  Cleaning up all coding agents"
echo "=============================================="

for agent in "${AGENTS[@]}"; do
  AGENT_DIR="${SCRIPT_DIR}/${agent}"
  if [ ! -d "$AGENT_DIR" ]; then
    echo "  SKIP: ${agent}/ not found"
    continue
  fi

  if [ ! -f "${AGENT_DIR}/cleanup.py" ]; then
    echo "  SKIP: ${agent}/cleanup.py not found"
    continue
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ${agent}: cleanup.py"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  (cd "$AGENT_DIR" && python cleanup.py) || echo "  WARNING: ${agent} cleanup failed, continuing..."
done

echo ""
echo "=============================================="
echo "  All agent runtimes removed."
echo "  To remove shared infra: cd infra && ./cleanup.sh"
echo "=============================================="
