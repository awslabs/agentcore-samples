#!/bin/bash

# Deploy the SDK agent implementation to ECR
echo "🚀 Deploying SDK agent (BedrockAgentCoreApp)..."

# Configuration - Get project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"  # Go up two levels to reach AgentCore root
RUNTIME_DIR="$(dirname "$SCRIPT_DIR")"  # agentcore-runtime directory

# Load configuration from unified config system
CONFIG_DIR="${PROJECT_DIR}/config"
BASE_SETTINGS="${CONFIG_DIR}/static-config.yaml"

if command -v yq >/dev/null 2>&1; then
    REGION=$(yq eval '.aws.region' "${BASE_SETTINGS}")
    ACCOUNT_ID=$(yq eval '.aws.account_id' "${BASE_SETTINGS}")
else
    echo "⚠️  yq not found, using grep/sed fallback"
    # Fallback: extract from YAML using grep/sed
    REGION=$(grep "region:" "${BASE_SETTINGS}" | head -1 | sed 's/.*region: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    ACCOUNT_ID=$(grep "account_id:" "${BASE_SETTINGS}" | head -1 | sed 's/.*account_id: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
fi
ECR_REPO="bac-runtime-repo-sdk"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

# Get AWS credentials from SSO
echo "🔐 Getting AWS credentials..."
if [ -n "$AWS_PROFILE" ]; then
    echo "Using AWS profile: $AWS_PROFILE"
else
    echo "Using default AWS credentials"
fi

# Use configured AWS profile if specified in static config
AWS_PROFILE_CONFIG=$(grep "aws_profile:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*aws_profile: *["'\'']*\([^"'\''#]*\)["'\'']*.*$/\1/' | xargs 2>/dev/null)
if [[ -n "$AWS_PROFILE_CONFIG" && "$AWS_PROFILE_CONFIG" != "\"\"" && "$AWS_PROFILE_CONFIG" != "''" ]]; then
    echo "Using configured AWS profile: $AWS_PROFILE_CONFIG"
    export AWS_PROFILE="$AWS_PROFILE_CONFIG"
fi

# Login to ECR
echo "🔑 Logging into ECR..."
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${ECR_URI}

# Check if repository exists, create if not
echo "📦 Checking ECR repository..."
if ! aws ecr describe-repositories --repository-names ${ECR_REPO} --region ${REGION} >/dev/null 2>&1; then
    echo "📦 Creating ECR repository: ${ECR_REPO}"
    aws ecr create-repository --repository-name ${ECR_REPO} --region ${REGION}
else
    echo "✅ ECR repository exists: ${ECR_REPO}"
fi

# Build ARM64 image using SDK Dockerfile
echo "🔨 Building ARM64 image..."
cd "${PROJECT_DIR}"
docker build --platform linux/arm64 -f agentcore-runtime/deployment/Dockerfile.sdk -t ${ECR_REPO}:latest .

# Tag for ECR
echo "🏷️  Tagging image..."
docker tag ${ECR_REPO}:latest ${ECR_URI}:latest

# Push to ECR
echo "📤 Pushing to ECR..."
docker push ${ECR_URI}:latest

# Update dynamic configuration with ECR URI
echo "📝 Updating dynamic config with ECR URI..."
DYNAMIC_CONFIG="${CONFIG_DIR}/dynamic-config.yaml"
if command -v yq >/dev/null 2>&1; then
    # Ensure the runtime.sdk_agent section exists
    yq eval '.runtime.sdk_agent.ecr_uri = "'"${ECR_URI}:latest"'"' -i "${DYNAMIC_CONFIG}"
    echo "   ✅ Dynamic config updated with ECR URI"
else
    echo "   ⚠️  yq not found. ECR URI will be updated by Python deployment script"
    echo "   📝 ECR URI: ${ECR_URI}:latest"
fi

echo "✅ SDK agent deployed to: ${ECR_URI}:latest"
echo ""

# Automatically run the runtime deployment script
echo "🚀 Running runtime deployment script..."
echo "   Executing: python3 deploy-sdk-runtime.py"
echo ""

cd "${SCRIPT_DIR}"
if python3 deploy-sdk-runtime.py; then
    echo ""
    echo "🎉 SDK Agent Deployment Complete!"
    echo "================================="
    echo "✅ ECR image deployed: ${ECR_URI}:latest"
    echo "✅ AgentCore runtime created and configured"
    echo ""
    echo "📋 What was deployed:"
    echo "   • Docker image built and pushed to ECR"
    echo "   • AgentCore runtime instance created"
    echo "   • OAuth integration with bac-identity-provider-okta"
    echo "   • MCP client for bac-gtw gateway"
    echo "   • AgentCore Memory integration"
    echo ""
    echo "💻 Your SDK agent is now ready to use OAuth2 tokens and MCP gateway!"
    echo "   Uses BedrockAgentCoreApp framework with @entrypoint decorator"
    echo "   Connect to the MCP gateway for tool access"
else
    echo ""
    echo "❌ Runtime deployment failed"
    echo "Please check the error messages above and try running manually:"
    echo "   python3 deploy-sdk-runtime.py"
    exit 1
fi