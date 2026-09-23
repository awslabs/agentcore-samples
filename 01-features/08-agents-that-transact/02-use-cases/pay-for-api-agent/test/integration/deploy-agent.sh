#!/usr/bin/env bash
# Deploy the Pay for API buyer agent to AgentCore Runtime via AWS CDK.
#
# The agent container image is built in AWS CodeBuild (not on this
# machine) so no Docker install is required. `cdk deploy` uploads
# agent/container/ as an S3 asset, CodeBuild pulls it, builds + pushes
# to ECR, and the Runtime resource pulls from there on invoke.
#
# Prerequisites:
#   - AWS CLI v2 configured (aws configure)
#   - AWS CDK v2 installed (npm install -g aws-cdk)
#   - Python 3.10+ with pip (for the CDK Python dependencies)
#
# Usage (from anywhere):
#   bash test/integration/deploy-agent.sh
#
# Writes outputs to agent/cdk/outputs.json. The notebook's §8 reads that
# file to pick up the Runtime ARN.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USE_CASE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CDK_DIR="${USE_CASE_ROOT}/agent/cdk"
CONTAINER_DIR="${USE_CASE_ROOT}/agent/container"

# Pull region from .env so it matches whatever the notebook provisioned.
if [ -f "${USE_CASE_ROOT}/.env" ]; then
    # Guard against unreplaced placeholders from env-sample.txt.
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

echo "── Pay for API — Agent Deploy ─────────────────────────────"
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
pip install --quiet --upgrade pip
pip install --quiet -r "${CDK_DIR}/requirements.txt"

# ── 2. Bootstrap (idempotent) ──
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region "${REGION}" >/dev/null 2>&1; then
    echo ""
    echo "Bootstrapping CDK for ${ACCOUNT_ID}/${REGION}..."
    (cd "${CDK_DIR}" && cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}")
else
    echo "CDK already bootstrapped for ${ACCOUNT_ID}/${REGION}."
fi

# ── 3. Deploy ──
echo ""
echo "Deploying AgentCorePaymentsBuyerAgentStack..."
echo "(CDK synth + asset upload + CodeBuild run — typically 5–8 min on the"
echo " first deploy, ~2 min on subsequent runs if nothing changed.)"
(cd "${CDK_DIR}" && cdk deploy --require-approval never --outputs-file ./outputs.json)

RUNTIME_ARN="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsBuyerAgentStack"]["AgentRuntimeArn"])')"
RUNTIME_ID="$(python3 -c 'import json; print(json.load(open("'"${CDK_DIR}"'/outputs.json"))["AgentCorePaymentsBuyerAgentStack"]["AgentRuntimeId"])')"

echo ""
echo "── Deploy Complete ─────────────────────────────────────────"
echo "✅ AgentRuntimeArn: ${RUNTIME_ARN}"
echo "   AgentRuntimeId: ${RUNTIME_ID}"
echo ""
echo "The notebook §8 reads agent/cdk/outputs.json to pick up these values."
