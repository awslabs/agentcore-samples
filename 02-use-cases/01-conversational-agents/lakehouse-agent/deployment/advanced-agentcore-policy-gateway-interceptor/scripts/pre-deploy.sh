#!/bin/bash
# Pre-deploy script for the advanced AgentCore Policy + Interceptor CDK sample.
#
# Combines the three preparation steps required before `cdk deploy`:
#   1. Generate cdk.json from SSM Parameter Store (filled by Phase 1 deployment)
#   2. Detach Interceptors from the Gateway
#        (Cedar Policy validation sends internal MCP requests signed with
#         SigV4 — they fail on JWT-validating Interceptors. Once detached,
#         CDK re-attaches both Interceptors + Policy Engine in one call.)
#   3. Overwrite the Phase 1 Request Interceptor Lambda source with the
#      Design 3 version (adds USER_GEOGRAPHY injection) and redeploy it.
#
# Usage:
#   cd 02-use-cases/lakehouse-agent/deployment/advanced-agentcore-policy-gateway-interceptor
#   AWS_REGION=us-east-1 bash scripts/pre-deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CDK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# deployment/advanced-agentcore-policy-gateway-interceptor/scripts
#   -> deployment/advanced-agentcore-policy-gateway-interceptor
#   -> deployment
#   -> lakehouse-agent
PROJECT_DIR="$(cd "$CDK_DIR/../.." && pwd)"

REGION="${AWS_REGION:-us-east-1}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"

echo "============================================================"
echo "Pre-deploy for advanced AgentCore Policy CDK"
echo "Region: $REGION"
echo "============================================================"

# ── Step 1: Generate cdk.json ─────────────────────────────────
echo ""
echo "[1/3] Generating cdk.json from SSM Parameter Store..."
bash "$SCRIPT_DIR/generate-cdk-context.sh"

# ── Step 2: Detach Interceptors ───────────────────────────────
echo ""
echo "[2/3] Detaching Interceptors from Gateway..."
echo "  (Cedar Policy validation sends internal MCP requests with SigV4,"
echo "   which fail on JWT-validating Interceptors.)"
python3 "$SCRIPT_DIR/detach-interceptors.py"

# ── Validate: Response Interceptor Lambda must exist ─────────
echo ""
echo "[Validate] Checking Response Interceptor Lambda exists in $REGION..."
if ! aws lambda get-function \
    --function-name lakehouse-gateway-response-interceptor \
    --region "$REGION" >/dev/null 2>&1; then
    echo "  ERROR: 'lakehouse-gateway-response-interceptor' not found in $REGION."
    echo ""
    echo "  CDK deploy will re-attach both interceptors to the Gateway."
    echo "  If the Response Interceptor Lambda does not exist, all tool calls"
    echo "  will fail with HTTP 500."
    echo ""
    echo "  Deploy it first (Phase 1 Step 5b):"
    echo "    cd $PROJECT_DIR/deployment/5-gateway-setup/interceptor-response"
    echo "    AWS_REGION=$REGION ./deploy.sh"
    exit 1
fi
echo "  Response Interceptor Lambda exists."

# ── Step 3: Update Request Interceptor Lambda ─────────────────
echo ""
echo "[3/3] Updating Request Interceptor Lambda (Design 3 geography support)..."

SRC="$CDK_DIR/lambda/interceptor-request/lambda_function.py"
DST="$PROJECT_DIR/deployment/5-gateway-setup/interceptor-request/lambda_function.py"

if [ -f "$SRC" ]; then
    cp "$SRC" "$DST"
    echo "  Copied: $(basename "$SRC")"
    echo "  From: $SRC"
    echo "  To:   $DST"

    echo "  Deploying Lambda..."
    cd "$PROJECT_DIR/deployment/5-gateway-setup/interceptor-request"
    AWS_REGION="$REGION" ./deploy.sh
    cd "$CDK_DIR"
    echo "  Lambda updated."
else
    echo "  Skip: $SRC not found (Design 3 Lambda not prepared)"
fi

echo ""
echo "============================================================"
echo "Pre-deploy complete. Next step:"
echo "  npx cdk deploy --require-approval never"
echo "============================================================"
