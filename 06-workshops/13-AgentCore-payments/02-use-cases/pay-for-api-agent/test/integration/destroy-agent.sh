#!/usr/bin/env bash
# Tear down the Pay for API buyer agent runtime + its CloudFormation stack.
#
# Uses the CDK venv deploy-agent.sh created (or creates it on demand so the
# script works standalone). Idempotent — safe to re-run; if the stack is
# already gone, CDK reports "No stacks match the name pattern" and exits
# cleanly.
#
# Usage (from anywhere):
#   bash test/integration/destroy-agent.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CDK_DIR="${USE_CASE_ROOT}/agent/cdk"

# Activate the CDK venv created by deploy-agent.sh. If it's missing (e.g.
# user cleaned up artifacts), rebuild it so `cdk destroy` can synth the
# Python app.
if [ ! -d "${CDK_DIR}/.venv" ]; then
    echo "Creating Python venv for CDK..."
    python3 -m venv "${CDK_DIR}/.venv"
    # shellcheck disable=SC1091
    source "${CDK_DIR}/.venv/bin/activate"
    pip install --quiet --upgrade pip
    pip install --quiet -r "${CDK_DIR}/requirements.txt"
else
    # shellcheck disable=SC1091
    source "${CDK_DIR}/.venv/bin/activate"
fi

echo "Destroying AgentCorePaymentsBuyerAgentStack..."
(cd "${CDK_DIR}" && cdk destroy --force)

echo ""
echo "✅ Agent runtime stack destroyed."
