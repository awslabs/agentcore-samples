#!/usr/bin/env bash
#
# Full demo cycle: reset → train → wait → resolve → wait → auto-test
#
# Each step can also be run independently:
#   python hydration/1_reset.py
#   python hydration/4_set_mode.py human
#   python hydration/2_autoseed.py
#   (wait 20 min)
#   python hydration/3_autoresolve.py
#   (wait 20 min)
#   python hydration/4_set_mode.py auto
#   bash hydration/5_run_auto_scenarios.sh
#   python hydration/demo_scenarios.py run --scenario demo-delayed-repeat
#   python hydration/demo_scenarios.py run --scenario demo-clean-fire
#
# Usage:
#   bash hydration/full_cycle.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

export PYTHONPATH="$PROJECT_DIR/agent/src"
PY="${PROJECT_DIR}/.venv/bin/python"

WAIT_MINUTES=${WAIT_MINUTES:-20}

echo "============================================================"
echo "FULL DEMO CYCLE"
echo "  Wait time: ${WAIT_MINUTES} minutes between phases"
echo "  Started:   $(date)"
echo "============================================================"

echo ""
echo "=== STEP 1: Reset ==="
"$PY" hydration/1_reset.py

echo ""
echo "=== STEP 2: Set human mode ==="
"$PY" hydration/4_set_mode.py human

echo ""
echo "=== STEP 3: Seed training scenarios ==="
"$PY" hydration/2_autoseed.py

echo ""
echo "=== STEP 4: Waiting ${WAIT_MINUTES} minutes for extraction ==="
sleep $((WAIT_MINUTES * 60))

echo ""
echo "=== STEP 5: Auto-resolve (adjuster approves/denies) ==="
"$PY" hydration/3_autoresolve.py

echo ""
echo "=== STEP 6: Waiting ${WAIT_MINUTES} minutes for human-grounded extraction ==="
sleep $((WAIT_MINUTES * 60))

echo ""
echo "=== STEP 7: Set auto mode ==="
"$PY" hydration/4_set_mode.py auto

echo ""
echo "=== STEP 8: Run auto scenarios + demo scenarios ==="
bash hydration/5_run_auto_scenarios.sh

echo ""
echo "--- demo-delayed-repeat ---"
"$PY" hydration/demo_scenarios.py run --scenario demo-delayed-repeat

echo ""
echo "--- demo-clean-fire ---"
"$PY" hydration/demo_scenarios.py run --scenario demo-clean-fire

echo ""
echo "============================================================"
echo "FULL CYCLE COMPLETE — $(date)"
echo "============================================================"
