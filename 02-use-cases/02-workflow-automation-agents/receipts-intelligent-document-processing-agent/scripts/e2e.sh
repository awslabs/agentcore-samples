#!/bin/bash
set -uo pipefail   # NOT -e: we capture the pytest exit code explicitly below.

# ============================================================================
# One-shot, hands-free end-to-end verification against REAL AWS (no mocks).
#   deploy  ->  assert on live resources (pytest -m e2e)  ->  destroy
# A teardown trap runs destroy on ANY exit (pass or fail) so nothing leaks, and
# the script exits with the TEST result — a failed assertion fails the script
# (so CI is honest), regardless of whether teardown succeeded.
# Usage: ./scripts/e2e.sh [region]
# ============================================================================

REGION="${1:-us-west-2}"
export AWS_REGION="$REGION"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

DEPLOYED=0
teardown() {
  if [ "$DEPLOYED" = "1" ]; then
    echo "🧹 [e2e] tearing down (always runs)..."
    ./destroy.sh "$REGION" || echo "⚠️  destroy reported an error — check the account for residue."
  fi
}
trap teardown EXIT

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "🔎 [e2e] account=$ACCOUNT_ID region=$REGION"

echo "🚀 [e2e] deploying..."
if ! ./deploy.sh "$REGION"; then
  echo "❌ [e2e] deploy failed."
  exit 1   # DEPLOYED still 0 if deploy.sh bailed before creating the stack; trap is a no-op
fi
DEPLOYED=1

echo "✅ [e2e] asserting against live resources..."
AWS_REGION="$REGION" python3 -m pytest -m e2e -q
TEST_RC=$?   # capture BEFORE the trap's destroy can clobber $?

if [ "$TEST_RC" -eq 0 ]; then
  echo "✅ [e2e] live assertions passed."
else
  echo "❌ [e2e] live assertions FAILED (pytest rc=$TEST_RC)."
fi
exit "$TEST_RC"   # exit with the TEST result; trap runs destroy, then this code stands
