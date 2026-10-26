#!/usr/bin/env bash
#
# Run the 6 auto-mode test scenarios (excludes 2 live demo scenarios).
#
# Usage:
#   bash hydration/5_run_auto_scenarios.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/agent/src"
PY="${PROJECT_DIR}/.venv/bin/python"

echo "=== Running auto-mode scenarios ==="
echo "  (demo-delayed-repeat and demo-clean-fire reserved for live demo)"
echo ""

SCENARIOS="sarah-parking-collision marcus-fender-bender lisa-pipe-burst sarah-delayed-theft marcus-roof-no-docs lisa-sewer-backup"
for scenario in $SCENARIOS; do
  echo "--- $scenario ---"
  "$PY" hydration/run_claim.py --scenario "$scenario"
  sleep 2
  echo ""
done

echo "✅ All auto scenarios complete."
