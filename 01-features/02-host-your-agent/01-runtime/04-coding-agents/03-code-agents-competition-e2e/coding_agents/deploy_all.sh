#!/usr/bin/env bash
# Deploy all coding agents in sequence.
# Prerequisites: infra/setup.sh already ran (infra.config exists).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

AGENTS=(claude-code kiro codex cursor hermes open-code)

echo "=============================================="
echo "  Deploying all coding agents"
echo "=============================================="

for agent in "${AGENTS[@]}"; do
  AGENT_DIR="${SCRIPT_DIR}/${agent}"
  if [ ! -d "$AGENT_DIR" ]; then
    echo "  SKIP: ${agent}/ not found"
    continue
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ${agent}: setup.sh"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  (cd "$AGENT_DIR" && ./setup.sh)

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ${agent}: deploy.py"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  (cd "$AGENT_DIR" && python deploy.py)
done

echo ""
echo "=============================================="
echo "  All agents deployed."
echo "  Test: python claude-code/connect.py"
echo "=============================================="
