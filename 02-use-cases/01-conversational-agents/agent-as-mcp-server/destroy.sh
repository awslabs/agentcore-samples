#!/bin/bash
set -euo pipefail
# ============================================================================
# Agent as an MCP server — teardown
# Usage: ./destroy.sh [region]        (default: us-east-1)
#
# Deletes the CloudFormation stack (Runtime, both Gateways, Lambda, roles),
# the AgentCore Identity credential provider, and the Cognito User Pool that
# setup_cognito.sh created. Nothing else in the account is touched.
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
REGION="${1:-${AWS_REGION:-us-east-1}}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
STACK="AgentCore-agentAsMcpServer-dev"
STATE_FILE=".cognito-state.json"

if [ -f .env ]; then set -a; source .env; set +a; fi

echo "🧹 Tearing down agent-as-mcp-server in $REGION"

# ─── 1. CloudFormation stack ───────────────────────────────────────────────
if aws cloudformation describe-stacks --stack-name "$STACK" >/dev/null 2>&1; then
  echo "🗑️  Deleting stack $STACK (this takes a few minutes)..."
  aws cloudformation delete-stack --stack-name "$STACK"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK" || {
    echo "   ⚠️  Stack delete did not complete cleanly. Check the CloudFormation console for $STACK."
  }
  echo "   ✓ Stack deleted"
else
  echo "   Stack $STACK not found (already deleted?)"
fi

# ─── 2. Identity credential provider ───────────────────────────────────────
CREDENTIAL_NAME="${AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER:-expense-gateway-m2m}"
echo "🔑 Deleting Identity credential provider $CREDENTIAL_NAME..."
aws bedrock-agentcore-control delete-oauth2-credential-provider --name "$CREDENTIAL_NAME" >/dev/null 2>&1 \
  && echo "   ✓ Deleted" || echo "   (not found)"

# ─── 3. Cognito (only if this sample created it) ───────────────────────────
if [ -f "$STATE_FILE" ]; then
  POOL_ID=$(python3 -c "import json;print(json.load(open('$STATE_FILE'))['user_pool_id'])")
  DOMAIN=$(python3 -c "import json;print(json.load(open('$STATE_FILE'))['domain_prefix'])")
  echo "🔐 Deleting Cognito domain $DOMAIN and pool $POOL_ID..."
  aws cognito-idp delete-user-pool-domain --domain "$DOMAIN" --user-pool-id "$POOL_ID" >/dev/null 2>&1 || true
  aws cognito-idp delete-user-pool --user-pool-id "$POOL_ID" >/dev/null 2>&1 && echo "   ✓ Deleted" || echo "   (not found)"
  rm -f "$STATE_FILE"
else
  echo "🔐 No .cognito-state.json; skipping Cognito (not created by this sample)"
fi

# ─── 4. Local state ────────────────────────────────────────────────────────
rm -f agentcore/.cli/deployed-state.json agentcore/.env.local .env
echo ""
echo "✅ Teardown complete. (CloudWatch log groups and the CDK bootstrap stack are left in place.)"
