#!/bin/bash

# Deploy AgentCore Gateway for Visa B2B Payment Integration
# This script sets up the gateway that converts Visa B2B REST APIs into MCP tools

set -e

# Activate Python virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "❌ Python virtual environment not found"
    echo "Please run: ./setup-python-env.sh"
    exit 1
fi

echo "=========================================="
echo "Deploying AgentCore Gateway"
echo "=========================================="
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is required but not installed"
    exit 1
fi

# Check if boto3 is installed
if ! python3 -c "import boto3" 2>/dev/null; then
    echo "❌ Error: boto3 is required but not installed"
    echo "Install with: pip3 install boto3"
    exit 1
fi

# Generate OpenAPI spec with dynamic stub API URL
echo "Generating OpenAPI spec with stub API URL..."
./scripts/generate-gateway-openapi-spec.sh

# Check if OpenAPI spec was generated
if [ ! -f "../visa-b2b-spec/gateway/visa-b2b-stub-openapi.json" ]; then
    echo "❌ Error: Failed to generate OpenAPI spec"
    exit 1
fi
echo ""

# Run the setup script
echo "Running AgentCore Gateway setup script..."
echo ""
python3 scripts/setup-agentcore-gateway.py

# Check if successful
if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✓ AgentCore Gateway Deployed Successfully"
    echo "=========================================="
    echo ""
    echo "Configuration saved to: infrastructure/.agentcore-gateway-config.json"
    echo ""
    
    # Grant AgentCore Runtime role permission to invoke the Gateway
    echo "🔐 Granting AgentCore Runtime permission to invoke Gateway..."
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    GATEWAY_ID=$(cat .agentcore-gateway-config.json | python3 -c "import sys, json; print(json.load(sys.stdin)['gateway_id'])")
    
    # Find the AgentCore Runtime role (auto-created by SDK with random suffix)
    RUNTIME_ROLE_NAME=$(aws iam list-roles \
      --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime-us-east-1-')].RoleName" \
      --output text | head -1)
    
    if [ -n "$RUNTIME_ROLE_NAME" ]; then
        echo "Found Runtime role: $RUNTIME_ROLE_NAME"
        
        aws iam put-role-policy \
            --role-name "$RUNTIME_ROLE_NAME" \
            --policy-name "InvokeAgentCoreGateway" \
            --policy-document "{
                \"Version\": \"2012-10-17\",
                \"Statement\": [{
                    \"Effect\": \"Allow\",
                    \"Action\": [
                        \"bedrock-agentcore:InvokeGateway\",
                        \"bedrock-agentcore:GetGateway\",
                        \"bedrock-agentcore:ListGatewayTargets\"
                    ],
                    \"Resource\": \"arn:aws:bedrock-agentcore:us-east-1:${ACCOUNT_ID}:gateway/${GATEWAY_ID}\"
                }]
            }" 2>/dev/null || echo "✓ Policy already exists"
        
        echo "✓ AgentCore Runtime granted permission to invoke Gateway"
    else
        echo "⚠️  AgentCore Runtime role not found - deploy Payment Agent first"
    fi
    echo ""
    echo "To test the gateway:"
    echo "  python3 infrastructure/scripts/test-gateway-tools.py"
    echo ""
else
    echo ""
    echo "❌ Gateway deployment failed"
    exit 1
fi
