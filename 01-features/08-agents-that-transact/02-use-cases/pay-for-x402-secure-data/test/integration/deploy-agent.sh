#!/usr/bin/env bash
# Deploy the Pay for Secure Data (x402) agent to AgentCore Runtime via AWS CDK.
#
# The agent container image is built in AWS CodeBuild (not on this machine)
# so no Docker install is required. `cdk deploy` uploads agent/container/ as
# an S3 asset, CodeBuild pulls it, builds + pushes to ECR, and the Runtime
# resource pulls from there on invoke.
#
# The script sources .env so the payment resources (MANAGER_ARN,
# PAYMENT_CONNECTOR_ID) and the t54 x402-secure guardrail configuration flow
# into the Runtime's container environment. The per-invocation payment
# context (user/session/instrument) is supplied per request, not here.
#
# Prerequisites:
#   - AWS CLI v2 configured (aws configure)
#   - AWS CDK v2 installed (npm install -g aws-cdk@2.1131.0)
#   - Python 3.10+ with pip (for the CDK Python dependencies)
#   - §4 of the notebook has created a PaymentManager + Connector (so
#     MANAGER_ARN / PAYMENT_CONNECTOR_ID are set in .env)
#
# Usage (from anywhere):
#   bash test/integration/deploy-agent.sh
#
# Writes outputs to agent/cdk/outputs.json. The notebook's runtime section
# reads that file to pick up the Runtime ARN.

set -euo pipefail

# Keep common CLI locations on PATH for GUI-launched Jupyter/VS Code kernels
# (see setup-roles.sh) so aws / node / npx are found when run from a notebook.
export PATH="/usr/local/bin:/opt/homebrew/bin:${PATH}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CDK_DIR="${USE_CASE_ROOT}/agent/cdk"
CONTAINER_DIR="${USE_CASE_ROOT}/agent/container"

# Pull region + payment/guardrail config from .env so the deployed runtime
# matches whatever the notebook provisioned.
if [ -f "${USE_CASE_ROOT}/.env" ]; then
    if grep -q "<ACCOUNT_ID>" "${USE_CASE_ROOT}/.env"; then
        echo "❌ ${USE_CASE_ROOT}/.env still contains <ACCOUNT_ID> placeholders." >&2
        echo "   Run:  bash test/integration/setup-roles.sh" >&2
        echo "   before deploying the agent." >&2
        exit 1
    fi
    set -a
    # shellcheck disable=SC1091
    source "${USE_CASE_ROOT}/.env"
    set +a
fi

REGION="${AWS_REGION:-us-west-2}"

if [ -z "${MANAGER_ARN:-}" ] || [ -z "${PAYMENT_CONNECTOR_ID:-}" ]; then
    echo "⚠️  MANAGER_ARN / PAYMENT_CONNECTOR_ID are not set in .env." >&2
    echo "   Run §4 of the notebook (or setup) to create the PaymentManager" >&2
    echo "   and Connector before deploying, or the runtime will have no" >&2
    echo "   payment resources to sign against." >&2
    read -r -p "   Continue anyway? [y/N] " ok
    case "${ok}" in
        y | Y | yes | YES) ;;
        *) echo "   Aborted."; exit 1 ;;
    esac
fi

echo "── Pay for Secure Data (x402) — Agent Deploy ──────────────"
echo "Region:    ${REGION}"
echo "CDK:       ${CDK_DIR}"
echo "Container: ${CONTAINER_DIR}"
echo ""
echo "The container image is built in AWS CodeBuild (no Docker needed on"
echo "this machine). First run can take 4–6 minutes for the build; subsequent"
echo "deploys only rebuild if agent/container/ changed."
echo ""

# ── 1. CDK Python venv ──
if [ ! -d "${CDK_DIR}/.venv" ]; then
    echo "Creating Python venv for CDK..."
    python3 -m venv "${CDK_DIR}/.venv"
fi
# shellcheck disable=SC1091
source "${CDK_DIR}/.venv/bin/activate"

echo "Installing CDK Python dependencies..."
pip install --quiet pip==26.1.2
pip install --quiet -r "${CDK_DIR}/requirements.txt"

# ── CDK CLI ──
# The CDK CLI (npm package `aws-cdk`) and the library (`aws-cdk-lib`) version
# independently, so a globally-installed `cdk` can be older than the library
# pip just resolved and fail with "Cloud assembly schema version mismatch".
# Run the CLI via npx so it stays compatible with the installed library
# without requiring a global upgrade. Override with CDK_CLI in the environment
# (for example CDK_CLI="cdk") to use a pinned/global CLI instead.
CDK_CLI_CMD="${CDK_CLI:-npx --yes aws-cdk@2.1131.0}"
echo "Using CDK CLI: ${CDK_CLI_CMD}"

# ── 2. Bootstrap (idempotent) ──
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "${REGION}" >/dev/null 2>&1; then
    echo ""
    echo "Bootstrapping CDK for ${ACCOUNT_ID}/${REGION}..."
    (cd "${CDK_DIR}" && ${CDK_CLI_CMD} bootstrap "aws://${ACCOUNT_ID}/${REGION}")
else
    echo "CDK already bootstrapped for ${ACCOUNT_ID}/${REGION}."
fi

# ── 3. Deploy ──
echo ""
echo "Deploying AgentCorePaymentsX402SecureDataAgentStack..."
echo "(CDK synth + asset upload + CodeBuild run — typically 5–8 min on the"
echo " first deploy, ~2 min on subsequent runs if nothing changed.)"
(cd "${CDK_DIR}" && ${CDK_CLI_CMD} deploy --require-approval never --outputs-file ./outputs.json)

RUNTIME_ARN="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsX402SecureDataAgentStack"]["AgentRuntimeArn"])')"
RUNTIME_ID="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsX402SecureDataAgentStack"]["AgentRuntimeId"])')"

echo ""
echo "── Deploy Complete ─────────────────────────────────────────"
echo "✅ AgentRuntimeArn: ${RUNTIME_ARN}"
echo "   AgentRuntimeId: ${RUNTIME_ID}"
echo ""
echo "The notebook's runtime section reads agent/cdk/outputs.json to pick up these values."
