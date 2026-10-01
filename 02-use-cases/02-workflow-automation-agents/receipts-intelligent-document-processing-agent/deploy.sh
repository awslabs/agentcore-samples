#!/bin/bash
set -euo pipefail

# ============================================================================
# Receipts IDP Agent — One-Command Deploy
# Usage: ./deploy.sh [region]
# Example: ./deploy.sh us-west-2
#
# Deploys one CloudFormation stack, AgentCore-ReceiptsAgent-dev:
#   - the AgentCore resources in agentcore/agentcore.json: two Runtimes, the Gateway
#     and its tools, the Cedar policy engine, the evaluators and a live evaluation config
#   - the supporting AWS infrastructure in the CDK infra-construct: DynamoDB, S3,
#     Cognito, SQS, AppConfig, EventBridge, KMS and the Lambdas
# Then applies the chat live evaluation config and seeds a sample user.
# ============================================================================

REGION="${1:-us-west-2}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"

echo "🚀 Deploying Receipts Agent to $REGION..."

# Check the prerequisites first, so a missing tool stops the script before the deploy
# rather than after it.
for tool in aws npm uv agentcore python3; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool (see README.md, Prerequisites)"; exit 1; }
done
python3 -c "import boto3" 2>/dev/null || {
  echo "Missing prerequisite: boto3 for python3 ($(command -v python3)). Install it with: python3 -m pip install boto3"
  exit 1
}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text) || {
  echo "No valid AWS credentials. Configure them, then run this script again."
  exit 1
}

# Step 0: write the deployment target
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
uv pip install --python .venv/bin/python -r requirements.txt --quiet
cd ../..

# Step 3: validate
echo "✅ Validating configuration..."
agentcore validate

# Step 4: deploy (with --yes, agentcore deploy also bootstraps CDK in the account and
# region the first time)
echo "🚀 Deploying via agentcore deploy..."
agentcore deploy --target dev --yes

# Step 5: configure chat live evaluation directly through the AgentCore API, outside CDK.
# CloudFormation does not yet accept these third-party evaluator IDs. The helper uses
# the chat Runtime and IAM role created by the stack, so it must run after deployment.
echo "Applying the chat online evaluation config..."
python3 scripts/chat_online_eval.py apply --region "$REGION"

# Step 6: seed sample data
echo "🌱 Seeding DynamoDB..."
python3 scripts/seed_dynamodb.py --region "$REGION"

echo ""
echo "✅ Done. Test with:"
echo "   python3 scripts/test_invoke.py --region $REGION"
echo "   (uploads the sample receipt and runs the pipeline once; files uploaded under receipts/ run automatically)"
echo "🧪 Local dev:  agentcore dev --no-browser"
