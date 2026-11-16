#!/bin/bash
set -euo pipefail
# ============================================================================
# Agent as an MCP server — one-command deploy
# Usage: ./deploy.sh [region]        (default: us-east-1)
#
# Deploys, in one CloudFormation stack via the AgentCore CLI:
#   - Cognito User Pool (script-managed): M2M client + user client + demo user
#   - AgentCore Identity credential provider (Cognito M2M, secret in token vault)
#   - Lambda with the mock expense tools
#   - AgentCore Gateway "ExpenseToolsGateway"  (Lambda target, CUSTOM_JWT / M2M client)
#   - AgentCore Runtime "expense_agent"        (MCP protocol, Strands agent inside)
#   - AgentCore Gateway "FrontDoorGateway"     (Runtime target, CUSTOM_JWT / user client)
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
REGION="${1:-${AWS_REGION:-us-east-1}}"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION" CDK_DEFAULT_REGION="$REGION"

echo "🚀 Deploying agent-as-mcp-server to $REGION"
echo ""

# ─── Step 0: account + deployment target ───────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
cat > agentcore/aws-targets.json <<EOF
[
  { "name": "dev", "account": "$ACCOUNT_ID", "region": "$REGION" }
]
EOF
echo "📋 Target: $ACCOUNT_ID / $REGION"

# ─── Step 1: Cognito ───────────────────────────────────────────────────────
if [ -f .env ]; then set -a; source .env; set +a; fi
# Trust only a pool that actually exists in this region (values may be stale after destroy.sh).
POOL_OK=false
if [ -n "${COGNITO_USER_POOL_ID:-}" ] && [ "${COGNITO_USER_POOL_ID}" != "PLACEHOLDER" ] \
   && aws cognito-idp describe-user-pool --user-pool-id "$COGNITO_USER_POOL_ID" --region "$REGION" >/dev/null 2>&1; then
  POOL_OK=true
fi
if [ "$POOL_OK" != "true" ]; then
  echo ""
  echo "🔐 Step 1: Creating Cognito resources (none found for $REGION)..."
  ./scripts/setup_cognito.sh "$REGION"
  set -a; source .env; set +a
else
  echo "🔐 Step 1: Cognito already configured (pool ${COGNITO_USER_POOL_ID})"
fi

# The credential provider is created by the CLI before CDK runs, so its discovery
# URL can't be patched in cdk-stack.ts like the gateway authorizers. Fill it in here.
python3 - "$COGNITO_DISCOVERY_URL" <<'PY'
import json, sys
path = "agentcore/agentcore.json"
spec = json.load(open(path))
for cred in spec.get("credentials", []):
    cred["discoveryUrl"] = sys.argv[1]
with open(path, "w") as f:
    json.dump(spec, f, indent=2)
    f.write("\n")
PY

# ─── Step 2: Identity credential provider (M2M secret -> token vault) ──────
# The credential "expense-gateway-m2m" is declared in agentcore.json. The CLI reads
# its client id/secret from agentcore/.env.local (gitignored) and stores them in the
# AgentCore Identity token vault during deploy. Write them on every run so a new
# Cognito pool always replaces stale values.
echo ""
echo "🔑 Step 2: Staging Cognito M2M credential for AgentCore Identity..."
umask 077
cat > agentcore/.env.local <<EOF
AGENTCORE_CREDENTIAL_EXPENSE_GATEWAY_M2M_CLIENT_ID=${AGENTCORE_GATEWAY_CLIENT_ID}
AGENTCORE_CREDENTIAL_EXPENSE_GATEWAY_M2M_CLIENT_SECRET=${AGENTCORE_GATEWAY_CLIENT_SECRET}
EOF
echo "   ✓ agentcore/.env.local written (gitignored)"

# ─── Step 3: dependencies ──────────────────────────────────────────────────
echo ""
echo "📦 Step 3: Installing dependencies..."
( cd agentcore/cdk && [ -d node_modules ] || npm install --quiet )
( cd app/expense_agent && uv sync --quiet )

# ─── Step 4: validate + bootstrap + deploy ─────────────────────────────────
echo ""
echo "✅ Step 4: Validating agentcore.json..."
agentcore validate

echo ""
echo "🏗️  Bootstrapping CDK (no-op if already done)..."
( cd agentcore/cdk && npx cdk bootstrap "aws://$ACCOUNT_ID/$REGION" --quiet 2>/dev/null || true )

echo ""
echo "🚀 Step 5: agentcore deploy..."
export COGNITO_DISCOVERY_URL AGENTCORE_GATEWAY_CLIENT_ID FRONTDOOR_USER_CLIENT_ID
agentcore deploy --target dev --yes

# ─── Done ──────────────────────────────────────────────────────────────────
STACK="AgentCore-agentAsMcpServer-dev"
FRONTDOOR_URL=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='FrontDoorMcpUrl'].OutputValue" --output text)
RUNTIME_MCP=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='RuntimeMcpEndpoint'].OutputValue" --output text)

echo ""
echo "✅ Deployed."
echo ""
echo "── Connect from Claude (OAuth, no AWS credentials) ──────────────────────"
echo "   Custom connector URL : $FRONTDOOR_URL"
echo "   Sign in as           : ${DEMO_USERNAME}  /  see DEMO_PASSWORD in .env"
echo ""
echo "── Connect from Kiro / Claude Code (SigV4 via mcp-proxy-for-aws) ────────"
echo "   uvx mcp-proxy-for-aws@latest \"$RUNTIME_MCP\""
echo ""
echo "── Smoke test ───────────────────────────────────────────────────────────"
echo "   uv run --no-project --with-requirements scripts/requirements.txt python scripts/test_frontdoor.py --region $REGION"
echo ""
echo "🧹 Teardown: ./destroy.sh $REGION"
