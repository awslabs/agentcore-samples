#!/usr/bin/env bash
#
# Runner: executes all deployment validation tests and reports aggregate results.
#
# Usage:
#   bash tests/deployment/run_all.sh [--region us-east-1]
#
set -uo pipefail

if [[ "${1:-}" == "--region" ]] && [[ -n "${2:-}" ]]; then
  export TEST_REGION="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()

echo "==========================================="
echo "  Deployment Validation"
echo "==========================================="

for test_file in "$SCRIPT_DIR"/test_*.sh; do
  echo ""
  output=$(bash "$test_file" 2>&1) || true
  echo "$output"

  pass=$(echo "$output" | grep "Results:" | grep -oE "[0-9]+ passed" | grep -oE "[0-9]+" || echo "0")
  fail=$(echo "$output" | grep "Results:" | grep -oE "[0-9]+ failed" | grep -oE "[0-9]+" || echo "0")
  TOTAL_PASS=$((TOTAL_PASS + pass))
  TOTAL_FAIL=$((TOTAL_FAIL + fail))

  if [[ $fail -gt 0 ]]; then
    FAILED_SUITES+=("$(basename "$test_file")")
  fi
done

echo ""
echo "==========================================="
echo "  Total: $TOTAL_PASS passed, $TOTAL_FAIL failed"
if [[ ${#FAILED_SUITES[@]} -gt 0 ]]; then
  echo "  Failed: ${FAILED_SUITES[*]}"
fi
echo "==========================================="

if [[ $TOTAL_FAIL -gt 0 ]]; then
  exit 1
fi
