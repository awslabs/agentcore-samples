#!/bin/bash
set -euo pipefail

# ============================================================================
# Event-Driven Claims Agent — One-Command Deploy
# Usage: ./deploy.sh [region]
# Example: ./deploy.sh us-west-2
#
# Deploys:
# - Cognito User Pool (interactive — creates if needed, or uses existing)
# - AgentCore Identity credential (registers Cognito as OAuth provider)
# - Infrastructure (DynamoDB, S3, SNS, EventBridge) via CDK
# - 7 Lambda functions (6 tools + 1 trigger)
# - AgentCore Runtime (dual-agent, SigV4 inbound, Identity-managed outbound)
# - AgentCore Gateway (MCP, 6 targets, CUSTOM_JWT from Cognito)
# - AgentCore Memory (SEMANTIC + SUMMARIZATION)
# - AgentCore Policy Engine (Cedar: AllowAll + BlockExcessiveClaims)
# - AgentCore Online Evaluation (built-in + custom LLM-as-judge)
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="${1:-us-west-2}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"

# Use Finch or Docker for container builds
export CDK_DOCKER="${CDK_DOCKER:-docker}"

echo "🚀 Deploying Claims Agent to $REGION..."
echo ""

# ─── Load .env if it exists ────────────────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a; source "$ENV_FILE"; set +a
fi

# ─── Step 0: Configure deployment target ───────────────────────────────────
echo "📋 Step 0: Configuring deployment target..."
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "dev",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
echo "   Target: $ACCOUNT_ID / $REGION"
echo ""

# ─── Step 1: Cognito Setup (interactive) ──────────────────────────────────
echo "🔐 Step 1: Checking MCP Gateway auth (Cognito)..."

# Check if Cognito values are configured (not PLACEHOLDER and not empty)
NEEDS_COGNITO=false
if [ "${AGENTCORE_GATEWAY_CLIENT_ID:-PLACEHOLDER}" = "PLACEHOLDER" ] || \
   [ "${AGENTCORE_GATEWAY_CLIENT_ID:-}" = "" ] || \
   [ "${COGNITO_DISCOVERY_URL:-PLACEHOLDER}" = "PLACEHOLDER" ] || \
   [ "${COGNITO_DISCOVERY_URL:-}" = "" ]; then
  NEEDS_COGNITO=true
fi

# Detect region mismatch: existing Cognito values point to a different region
if [ "$NEEDS_COGNITO" = "false" ] && [ -n "${COGNITO_DISCOVERY_URL:-}" ]; then
  COGNITO_REGION=$(echo "$COGNITO_DISCOVERY_URL" | grep -oP '(?<=cognito-idp\.)[^.]+' 2>/dev/null || echo "$COGNITO_DISCOVERY_URL" | sed -n 's|.*cognito-idp\.\([^.]*\)\..*|\1|p')
  if [ -n "$COGNITO_REGION" ] && [ "$COGNITO_REGION" != "$REGION" ]; then
    echo ""
    echo "   ⚠️  Cognito pool is in $COGNITO_REGION but deploying to $REGION."
    echo "   A new Cognito pool is needed in the target region."
    NEEDS_COGNITO=true
  fi
fi

if [ "$NEEDS_COGNITO" = "true" ]; then
  echo ""
  echo "   ⚠️  Gateway auth not configured (Cognito values are placeholders)."
  echo ""
  echo "   The MCP Gateway requires a Cognito User Pool for CUSTOM_JWT auth."
  echo "   Options:"
  echo "     1) Create a Cognito User Pool automatically (recommended)"
  echo "     2) Exit and configure manually in .env"
  echo ""

  # Check if running non-interactively (piped or CI)
  if [ -t 0 ]; then
    read -rp "   Create Cognito User Pool now? [Y/n] " REPLY
    REPLY="${REPLY:-Y}"
  else
    REPLY="Y"
    echo "   (Non-interactive mode: auto-creating Cognito)"
  fi

  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    echo ""
    bash "$SCRIPT_DIR/scripts/setup_cognito.sh" "$REGION"
    echo ""
    # Reload .env with new values
    if [ -f "$ENV_FILE" ]; then
      set -a; source "$ENV_FILE"; set +a
    fi
  else
    echo ""
    echo "   ❌ Cannot deploy without Gateway auth configuration."
    echo "   Please either:"
    echo "     - Run: ./scripts/setup_cognito.sh $REGION"
    echo "     - Or fill in AGENTCORE_GATEWAY_* values in .env manually"
    exit 1
  fi
fi

echo "   ✓ Cognito configured (Client ID: ${AGENTCORE_GATEWAY_CLIENT_ID:0:8}...)"
echo ""

# ─── Step 2: Register credential with AgentCore Identity ──────────────────
echo "🔑 Step 2: Registering OAuth credential with AgentCore Identity..."
CREDENTIAL_NAME="${AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER:-cognito-gateway-m2m}"

# agentcore add credential registers the secret in the Identity token vault.
# If it already exists, this is a no-op (the CLI handles idempotency).
agentcore add credential \
  --name "$CREDENTIAL_NAME" \
  --type oauth \
  --discovery-url "$COGNITO_DISCOVERY_URL" \
  --client-id "$AGENTCORE_GATEWAY_CLIENT_ID" \
  --client-secret "$AGENTCORE_GATEWAY_CLIENT_SECRET" \
  --scopes "${AGENTCORE_GATEWAY_OAUTH_SCOPES:-agentcore/invoke}" 2>/dev/null || {
    echo "   ⚠️  Credential registration returned non-zero (may already exist). Continuing..."
  }
echo "   ✓ Credential provider: $CREDENTIAL_NAME"
echo ""

# ─── Step 3: Install CDK dependencies ─────────────────────────────────────
echo "📦 Step 3: Installing CDK dependencies..."
cd agentcore/cdk
if [ ! -d "node_modules" ]; then
  npm install --quiet
fi
cd ../..
echo ""

# ─── Step 4: Install agent Python dependencies ────────────────────────────
echo "🐍 Step 4: Installing agent dependencies..."
cd app/claimsagent
if [ ! -d ".venv" ]; then
  uv venv
fi
uv sync --quiet 2>/dev/null || uv pip install -r requirements.txt --quiet
cd ../..
echo ""

# ─── Step 5: Validate agentcore.json ──────────────────────────────────────
echo "✅ Step 5: Validating configuration..."
agentcore validate
echo ""

# ─── Step 6: Bootstrap CDK ────────────────────────────────────────────────
echo "🏗️  Step 6: Checking CDK bootstrap..."
cdk bootstrap aws://$ACCOUNT_ID/$REGION --quiet 2>/dev/null || true
echo ""

# ─── Step 7: Deploy via AgentCore CLI ─────────────────────────────────────
echo "🚀 Step 7: Deploying via agentcore deploy..."
# Export Cognito values so CDK can read them during synthesis
export COGNITO_DISCOVERY_URL="${COGNITO_DISCOVERY_URL}"
export COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-}"
export AGENTCORE_GATEWAY_CLIENT_ID="${AGENTCORE_GATEWAY_CLIENT_ID}"
export AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER="${CREDENTIAL_NAME}"

agentcore deploy --target dev --yes
echo ""

# ─── Step 8: Seed DynamoDB ─────────────────────────────────────────────────
echo "🌱 Step 8: Seeding DynamoDB..."
python3 scripts/seed_dynamodb.py --region "$REGION"
echo ""

# ─── Done ─────────────────────────────────────────────────────────────────
echo "✅ Done! Claims Agent deployed to $REGION"
echo ""
echo "📋 Test with:"
echo "   python3 scripts/test_invoke.py --region $REGION"
echo ""
echo "🛡️  Test Cedar policy (should block \$100k+ claims):"
echo "   python3 scripts/test_invoke.py --region $REGION --prompt \"File a claim for POL-12345. Car totaled. \$150000 damage.\""
echo ""
echo "🔭 Enable full observability (optional — adds Gateway/Memory trace + log delivery):"
echo "   python3 scripts/enable_observability.py --region $REGION --stack-name AgentCore-ClaimsAgent-dev"
echo ""
echo "🧹 Teardown:"
echo "   ./scripts/destroy.sh $REGION"
