#!/usr/bin/env bash
# Tear down the Pay for Secure Data (x402) agent runtime + its CloudFormation
# stack.
#
# Uses the CDK venv deploy-agent.sh created (or creates it on demand so the
# script works standalone). Idempotent — safe to re-run; if the stack is
# already gone, CDK reports "No stacks match the name pattern" and exits
# cleanly.
#
# Usage (from anywhere):
#   bash test/integration/destroy-agent.sh

set -euo pipefail

# Keep common CLI locations on PATH for GUI-launched Jupyter/VS Code kernels
# (see setup-roles.sh) so aws / node / npx are found when run from a notebook.
export PATH="/usr/local/bin:/opt/homebrew/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CDK_DIR="${USE_CASE_ROOT}/agent/cdk"

# Activate the CDK venv created by deploy-agent.sh. If it's missing (e.g.
# artifacts were cleaned up), rebuild it so `cdk destroy` can synth the app.
if [ ! -d "${CDK_DIR}/.venv" ]; then
    echo "Creating Python venv for CDK..."
    python3 -m venv "${CDK_DIR}/.venv"
    # shellcheck disable=SC1091
    source "${CDK_DIR}/.venv/bin/activate"
    pip install --quiet pip==26.1.2
    pip install --quiet -r "${CDK_DIR}/requirements.txt"
else
    # shellcheck disable=SC1091
    source "${CDK_DIR}/.venv/bin/activate"
fi

# Run the CDK CLI via npx so it stays compatible with the installed
# aws-cdk-lib (see deploy-agent.sh). Override with CDK_CLI to use a global CLI.
CDK_CLI_CMD="${CDK_CLI:-npx --yes aws-cdk@2.1131.0}"

echo "Destroying AgentCorePaymentsX402SecureDataAgentStack..."
(cd "${CDK_DIR}" && ${CDK_CLI_CMD} destroy --force)

echo ""
echo "✅ Agent runtime stack destroyed."
