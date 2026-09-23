#!/bin/bash
set -euo pipefail

# ============================================================================
# Event-Driven Claims Agent — One-Command Teardown
# Usage: ./destroy.sh [region]
# Example: ./destroy.sh us-west-2
#
# Destroys ALL resources created by deploy.sh:
# - AgentCore Runtime, Gateway, Memory, PolicyEngine, OnlineEval
# - Infrastructure (DynamoDB, S3, SNS, Cognito, EventBridge, Lambda)
#
# S3 buckets and DynamoDB tables have RemovalPolicy.DESTROY + autoDeleteObjects,
# so all data is permanently deleted. This is NOT reversible.
# ============================================================================

REGION="${1:-us-west-2}"
export AWS_REGION="$REGION"
export AWS_DEFAULT_REGION="$REGION"
export CDK_DEFAULT_REGION="$REGION"

# Use Finch or Docker for container builds
export CDK_DOCKER="${CDK_DOCKER:-docker}"

echo "🗑️  Destroying Claims Agent in $REGION..."
echo ""

# Ensure aws-targets.json exists with correct region
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ ! -f agentcore/aws-targets.json ]; then
  cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "dev",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
  echo "  Generated aws-targets.json (account: $ACCOUNT_ID, region: $REGION)"
  echo ""
fi

# Install CDK dependencies if needed
cd agentcore/cdk
if [ ! -d "node_modules" ]; then
  echo "📦 Installing CDK dependencies..."
  npm install --quiet
fi

# Destroy via CDK
echo "💥 Destroying stack AgentCore-ClaimsAgent-dev..."
cdk destroy --all --force
cd ../..
echo ""

echo "✅ Done! All Claims Agent resources destroyed in $REGION."
echo ""
echo "To redeploy: ./deploy.sh $REGION"
