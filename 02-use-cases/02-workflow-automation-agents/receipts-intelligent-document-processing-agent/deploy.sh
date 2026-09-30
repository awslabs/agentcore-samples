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

echo "🚀 Deploying Receipts Agent to $REGION..."

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

# Step 6: the chat online evaluation config (managed third-party evaluators, which the
# CloudFormation schema does not accept yet; see scripts/chat_online_eval.py)
echo "Applying the chat online evaluation config..."
python3 scripts/chat_online_eval.py apply --region "$REGION"

# Step 7: seed sample data
echo "🌱 Seeding DynamoDB..."
python3 scripts/seed_dynamodb.py --region "$REGION"

echo ""
echo "✅ Done. Test with:"
echo "   python3 scripts/upload_sample_receipt.py --region $REGION"
echo "   python3 scripts/test_invoke.py --region $REGION --s3-uri <the s3:// URI it prints>"
echo "🧪 Local dev:  agentcore dev --no-browser"
