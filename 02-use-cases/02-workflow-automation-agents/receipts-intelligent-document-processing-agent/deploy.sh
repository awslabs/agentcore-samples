#!/bin/bash
set -euo pipefail

# ============================================================================
# Receipts IDP Agent — One-Command Deploy
# Usage: ./deploy.sh [region]
# Example: ./deploy.sh us-west-2
#
# PHASE 1 (walking skeleton) deploys:
#   - Supplementary infra (DynamoDB Users/Expenses/Merchants, S3 inbox, Cognito
#     M2M, L4 SQS) via the CDK infra-construct
#   - AgentCore Runtime (stub agent, Cognito auth, OTel observability) via agentcore.json
# Later phases add the Gateway tools, Cedar policy, the degradation ladder, the
# event-driven trigger, and Evaluations (see IMPLEMENTATION-PLAN.md).
# ============================================================================

REGION="${1:-us-west-2}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"

# Container engine for the CDK image build. Respect an explicit CDK_DOCKER; else
# prefer Docker, then fall back to Finch (both are supported — see ADR-0005).
# `agentcore dev` does NOT need this; only the full `deploy` builds the image.
if [ -z "${CDK_DOCKER:-}" ]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    export CDK_DOCKER=docker
  elif command -v finch >/dev/null 2>&1; then
    export CDK_DOCKER=finch
    echo "ℹ️  Using Finch as the container engine (CDK_DOCKER=finch)."
    echo "   Ensure the Finch VM is running: finch vm status  (start with: finch vm start)"
  else
    echo "❌ No container engine found. Install Docker or Finch (https://runfinch.com)." >&2
    exit 1
  fi
fi

echo "🚀 Deploying Receipts Agent to $REGION (engine: $CDK_DOCKER)..."

# Step 0: write the deployment target
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "dev",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
echo "  Target: $ACCOUNT_ID / $REGION"

# Step 1: CDK deps
echo "📦 Installing CDK dependencies..."
cd agentcore/cdk
[ -d node_modules ] || npm install --quiet
cd ../..

# Step 2: agent Python deps
echo "🐍 Installing agent dependencies..."
cd app/receiptsagent
[ -d .venv ] || uv venv
uv pip install -r requirements.txt --quiet
cd ../..

# Step 3: validate
echo "✅ Validating configuration..."
agentcore validate

# Step 4: bootstrap (first-time only)
echo "🏗️  Checking CDK bootstrap..."
cdk bootstrap "aws://$ACCOUNT_ID/$REGION" --quiet 2>/dev/null || true

# Step 5: deploy
echo "🚀 Deploying via agentcore deploy..."
agentcore deploy --target dev --yes

# Step 6: seed sample data
echo "🌱 Seeding DynamoDB..."
python3 scripts/seed_dynamodb.py --region "$REGION"

echo ""
echo "✅ Done. Test with:"
echo "   python3 scripts/test_invoke.py --region $REGION"
echo "🧪 Local dev:  agentcore dev --no-browser"
