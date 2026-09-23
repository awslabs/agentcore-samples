#!/bin/bash

# Deploy AgentCore Invoker Lambda Stack
# This Lambda bridges TypeScript backend with Python AgentCore SDK

set -e

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🚀 Deploying AgentCore Invoker Stack..."

# Check if infrastructure stack exists
echo "✓ Checking infrastructure stack..."
if ! aws cloudformation describe-stacks --stack-name RtpOverlayStack --region us-east-1 > /dev/null 2>&1; then
    echo "❌ Infrastructure stack not found"
    echo "Please deploy infrastructure first: ./deploy.sh"
    exit 1
fi

echo "✓ Infrastructure stack found"
echo ""

# Get VPC ID from infrastructure stack
echo "✓ Getting VPC configuration..."
VPC_ID=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
    --output text)

echo "  VPC ID: $VPC_ID"
echo ""

# Build TypeScript files
echo "📦 Building TypeScript..."
npm run build

# Deploy AgentCore Invoker stack
echo "🚀 Deploying AgentCore Invoker Lambda..."
echo "⏱️  This will take approximately 5-10 minutes..."
echo ""

npx cdk deploy AgentCoreInvokerStack \
    --app "npx ts-node bin/deploy-agentcore-invoker.ts" \
    --context vpcId="$VPC_ID" \
    --require-approval never

echo ""
echo "✅ AgentCore Invoker Stack deployed successfully!"
echo ""

# Get stack outputs
AGENTCORE_INVOKER_LAMBDA=$(aws cloudformation describe-stacks \
    --stack-name AgentCoreInvokerStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`AgentCoreInvokerLambdaName`].OutputValue' \
    --output text)

echo "📝 AgentCore Invoker Lambda: $AGENTCORE_INVOKER_LAMBDA"
echo ""

# Update RUNTIME_ARN environment variable from SSM Parameter Store
echo "📦 Updating AgentCore Invoker with Payment Agent ARN..."
PAYMENT_AGENT_ARN=$(aws ssm get-parameter \
    --name "/rtp-overlay/payment-agent-arn" \
    --query 'Parameter.Value' \
    --output text \
    --region us-east-1 2>/dev/null || echo "")

if [ -n "$PAYMENT_AGENT_ARN" ]; then
    echo "  Payment Agent ARN: $PAYMENT_AGENT_ARN"
    
    # Get current environment variables
    CURRENT_ENV=$(aws lambda get-function-configuration \
        --function-name $AGENTCORE_INVOKER_LAMBDA \
        --query 'Environment.Variables' \
        --output json \
        --region us-east-1)
    
    # Update RUNTIME_ARN
    UPDATED_ENV=$(echo $CURRENT_ENV | jq -c ". + {\"RUNTIME_ARN\": \"$PAYMENT_AGENT_ARN\"}")
    
    # Update Lambda
    aws lambda update-function-configuration \
        --function-name $AGENTCORE_INVOKER_LAMBDA \
        --environment "{\"Variables\":$UPDATED_ENV}" \
        --region us-east-1 \
        --output json > /dev/null
    
    echo "✓ AgentCore Invoker updated with Payment Agent ARN"
else
    echo "⚠️  Payment Agent ARN not found in SSM - will need to redeploy after Payment Agent is deployed"
fi

echo ""
echo "✅ AgentCore Invoker Deployed!"
echo ""

# ============================================================================
# Automatically redeploy Backend Lambda to import AgentCore Invoker name
# ============================================================================

echo "📦 Redeploying Backend Lambda to import AgentCore Invoker name..."
echo ""

cd "$SCRIPT_DIR"
./deploy-lambda.sh

echo ""
echo "✅ Deployment Complete!"
echo ""
echo "📝 Next steps:"
echo "1. Deploy Payment Agent: ./deploy-payment-agent.sh"
echo "2. Test payment processing from the frontend"
