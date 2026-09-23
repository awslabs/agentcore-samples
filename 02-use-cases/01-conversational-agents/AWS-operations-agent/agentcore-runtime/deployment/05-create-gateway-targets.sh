#!/bin/bash

# Create AgentCore Gateway and MCP targets using OAuth provider and MCP tool Lambda
echo "🚀 Creating AgentCore Gateway and MCP targets..."

# Configuration - Get project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"  # Go up two levels to reach AgentCore root
RUNTIME_DIR="$(dirname "$SCRIPT_DIR")"  # agentcore-runtime directory
CONFIG_DIR="${PROJECT_DIR}/config"

# Load configuration from YAML (fallback if yq not available)
if command -v yq >/dev/null 2>&1; then
    REGION=$(yq eval '.aws.region' "${CONFIG_DIR}/static-config.yaml")
    ACCOUNT_ID=$(yq eval '.aws.account_id' "${CONFIG_DIR}/static-config.yaml")
    ROLE_ARN=$(yq eval '.runtime.role_arn' "${CONFIG_DIR}/static-config.yaml")
    # Get gateway execution role from dynamic config
    GATEWAY_EXECUTION_ROLE_ARN=$(yq eval '.mcp_lambda.gateway_execution_role_arn' "${CONFIG_DIR}/dynamic-config.yaml")
else
    echo "⚠️  yq not found, using default values from existing config"
    # Fallback: extract from YAML using grep/sed
    REGION=$(grep "region:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*region: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    ACCOUNT_ID=$(grep "account_id:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*account_id: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    ROLE_ARN=$(grep "role_arn:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*role_arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    # Get gateway execution role from dynamic config
    GATEWAY_EXECUTION_ROLE_ARN=$(grep "gateway_execution_role_arn:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*gateway_execution_role_arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
fi

# Load OAuth provider configuration from dynamic config
if command -v yq >/dev/null 2>&1; then
    PROVIDER_ARN=$(yq eval '.oauth_provider.provider_arn' "${CONFIG_DIR}/dynamic-config.yaml")
    OKTA_DOMAIN=$(yq eval '.okta.domain' "${CONFIG_DIR}/static-config.yaml")
else
    PROVIDER_ARN=$(grep "provider_arn:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*provider_arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    OKTA_DOMAIN=$(grep "domain:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*domain: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
fi

if [[ -z "$PROVIDER_ARN" || "$PROVIDER_ARN" == "null" ]]; then
    echo "❌ OAuth provider ARN not found in configuration"
    echo "   Please run ./02-setup-oauth-provider.sh first"
    exit 1
fi

# Load Okta JWT configuration from static config
if command -v yq >/dev/null 2>&1; then
    JWT_DISCOVERY_URL=$(yq eval '.okta.jwt.discovery_url' "${CONFIG_DIR}/static-config.yaml")
    JWT_AUDIENCE=$(yq eval '.okta.jwt.audience' "${CONFIG_DIR}/static-config.yaml")
else
    JWT_DISCOVERY_URL=$(grep "discovery_url:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*discovery_url: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
    JWT_AUDIENCE=$(grep "audience:" "${CONFIG_DIR}/static-config.yaml" | head -1 | sed 's/.*audience: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/')
fi

# Load Lambda function configuration from dynamic config
if command -v yq >/dev/null 2>&1; then
    LAMBDA_FUNCTION_ARN=$(yq eval '.mcp_lambda.function_arn' "${CONFIG_DIR}/dynamic-config.yaml" 2>/dev/null)
else
    LAMBDA_FUNCTION_ARN=$(grep "function_arn:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*function_arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' 2>/dev/null)
fi

if [[ -z "$LAMBDA_FUNCTION_ARN" || "$LAMBDA_FUNCTION_ARN" == "null" ]]; then
    echo "❌ MCP Lambda function ARN not found in configuration"
    echo "   Please run ./03-deploy-mcp-tool-lambda.sh first"
    exit 1
fi

if [[ -z "$ROLE_ARN" || "$ROLE_ARN" == "null" ]]; then
    echo "❌ Gateway Execution Role ARN (bac-execution-role) not found in configuration"
    echo "   Please run ./01-prerequisites.sh first"
    exit 1
fi

# Configuration values (environment-agnostic)
GATEWAY_NAME="bac-gtw"
GATEWAY_DESCRIPTION="BAC Gateway for AWS operations via MCP"
TARGET_NAME="bac-tool"
TARGET_DESCRIPTION="BAC MCP Target with AWS service tools"

echo "📝 Configuration:"
echo "   Region: $REGION"
echo "   Account ID: $ACCOUNT_ID"
echo "   Gateway Name: $GATEWAY_NAME"
echo "   Target Name: $TARGET_NAME"
echo "   Gateway Role ARN (bac-execution-role): $ROLE_ARN"
echo "   Lambda Function Role ARN: $GATEWAY_EXECUTION_ROLE_ARN"
echo "   Provider ARN: $PROVIDER_ARN"
echo "   Lambda ARN: $LAMBDA_FUNCTION_ARN"
echo "   JWT Discovery URL: $JWT_DISCOVERY_URL"
echo "   JWT Audience: $JWT_AUDIENCE"
echo ""

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

# Path to gateway operations scripts
GATEWAY_OPS_DIR="${RUNTIME_DIR}/gateway-ops-scripts"

# Function to check if Python scripts are available
check_gateway_scripts() {
    if [[ ! -d "$GATEWAY_OPS_DIR" ]]; then
        echo "❌ Gateway operations scripts not found: $GATEWAY_OPS_DIR"
        return 1
    fi
    
    local required_scripts=("create-gateway.py" "create-target.py")
    for script in "${required_scripts[@]}"; do
        if [[ ! -f "${GATEWAY_OPS_DIR}/${script}" ]]; then
            echo "❌ Required script not found: ${GATEWAY_OPS_DIR}/${script}"
            return 1
        fi
    done
    
    echo "✅ Gateway operations scripts found"
    return 0
}

# Check if gateway operations scripts are available
echo "🔍 Checking gateway operations scripts..."
if ! check_gateway_scripts; then
    echo "❌ Gateway operations scripts not available"
    echo "   Expected location: $GATEWAY_OPS_DIR"
    exit 1
fi

# Activate virtual environment to ensure Python dependencies are available
echo "🐍 Activating Python virtual environment..."
cd "${PROJECT_DIR}" && source .venv/bin/activate

# Create the gateway using Python script
echo "🏗️  Creating AgentCore Gateway using Python script..."
cd "$GATEWAY_OPS_DIR"

GATEWAY_RESPONSE=$(python3 create-gateway.py \
    --name "$GATEWAY_NAME" \
    --description "$GATEWAY_DESCRIPTION" 2>&1)

if [[ $? -ne 0 ]]; then
    echo "❌ Failed to create gateway"
    echo "$GATEWAY_RESPONSE"
    exit 1
fi

echo "$GATEWAY_RESPONSE"

# Extract Gateway information from response (Python script outputs human-readable format)
GATEWAY_ID=$(echo "$GATEWAY_RESPONSE" | grep "   Gateway ID:" | sed 's/.*Gateway ID: *//' | tail -1 | tr -d '\n\r')
GATEWAY_URL=$(echo "$GATEWAY_RESPONSE" | grep "   Gateway URL:" | sed 's/.*Gateway URL: *//' | tail -1 | tr -d '\n\r')

if [[ -z "$GATEWAY_ID" ]]; then
    echo "⚠️  Could not extract Gateway ID from response"
    # Try to get gateway ID from list if creation was successful
    LIST_RESPONSE=$(python3 list-gateways.py 2>/dev/null || echo "")
    if [[ -n "$LIST_RESPONSE" ]]; then
        echo "🔍 Attempting to find gateway from list..."
        echo "$LIST_RESPONSE"
    fi
fi

# Get Gateway ARN from dynamic config (updated by Python script)
if command -v yq >/dev/null 2>&1; then
    GATEWAY_ARN=$(yq eval '.gateway.arn' "${CONFIG_DIR}/dynamic-config.yaml" 2>/dev/null)
else
    GATEWAY_ARN=$(grep "arn:" "${CONFIG_DIR}/dynamic-config.yaml" | head -1 | sed 's/.*arn: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' 2>/dev/null)
fi

echo "✅ Gateway creation process completed!"
if [[ -n "$GATEWAY_ID" ]]; then
    echo "   Gateway ID: $GATEWAY_ID"
fi

# Create the target using Python script
echo "🎯 Creating MCP target with AWS tools using Python script..."
echo "   Using Gateway ID: $GATEWAY_ID"
echo "   Using Lambda ARN: $LAMBDA_FUNCTION_ARN"

TARGET_RESPONSE=$(python3 create-target.py \
    --gateway-id "$GATEWAY_ID" \
    --lambda-arn "$LAMBDA_FUNCTION_ARN" \
    --name "$TARGET_NAME" \
    --description "$TARGET_DESCRIPTION" 2>&1)
if [[ $? -ne 0 ]]; then
    echo "❌ Failed to create target"
    echo "$TARGET_RESPONSE"
    exit 1
fi

echo "$TARGET_RESPONSE"

# Extract Target information from response
TARGET_ID=$(echo "$TARGET_RESPONSE" | grep "   Target ID:" | sed 's/.*Target ID: *//' | tail -1 | tr -d '\n\r')
TOOL_COUNT=$(echo "$TARGET_RESPONSE" | grep "   Tool Count:" | sed 's/.*Tool Count: *//' | tail -1 | tr -d '\n\r')

# Target ARN is not provided by AWS API, construct it manually
if [[ -n "$GATEWAY_ID" && -n "$TARGET_ID" ]]; then
    TARGET_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:gateway/${GATEWAY_ID}/target/${TARGET_ID}"
else
    TARGET_ARN="unknown"
fi

echo "✅ Target creation process completed!"
if [[ -n "$TARGET_ID" && "$TARGET_ID" != "unknown" ]]; then
    echo "   Target ID: $TARGET_ID"
fi

# Return to original directory
cd "${SCRIPT_DIR}"

echo ""
echo "🎉 AgentCore Gateway and Target Creation Complete!"
echo "======================================================"
echo ""
echo "📋 Created Resources:"
if [[ -n "$GATEWAY_ID" && "$GATEWAY_ID" != "unknown" ]]; then
    echo "   • Gateway: $GATEWAY_NAME ($GATEWAY_ID)"
else
    echo "   • Gateway: $GATEWAY_NAME (creation initiated)"
fi
if [[ -n "$TARGET_ID" && "$TARGET_ID" != "unknown" ]]; then
    echo "   • Target: $TARGET_NAME ($TARGET_ID)"
else
    echo "   • Target: $TARGET_NAME (creation initiated)"
fi
echo "   • Lambda Function: $LAMBDA_FUNCTION_ARN"
echo "   • OAuth Provider: $PROVIDER_ARN"
echo ""
echo "🔍 Check Status:"
echo "   • List gateways: cd ${GATEWAY_OPS_DIR} && python3 list-gateways.py"
echo "   • List targets: cd ${GATEWAY_OPS_DIR} && python3 list-targets.py"
if [[ -n "$GATEWAY_ID" && "$GATEWAY_ID" != "unknown" ]]; then
    echo "   • Gateway details: cd ${GATEWAY_OPS_DIR} && python3 get-gateway.py --gateway-id $GATEWAY_ID"
fi
echo ""
echo "🚀 Next Steps:"
echo "   • Deploy agent runtimes: ./05-deploy-diy.sh and ./06-deploy-sdk.sh"
echo "   • Test MCP connection to gateway when ready"
echo "================================================"
echo "✅ Gateway and target deployed and configured"
echo ""
echo "📋 Gateway Details:"
echo "   • Gateway ID: ${GATEWAY_ID:-unknown}"
echo "   • Gateway ARN: ${GATEWAY_ARN:-unknown}"
echo "   • Gateway URL: ${GATEWAY_URL:-unknown}"
echo "   • Gateway Name: $GATEWAY_NAME"
echo ""
echo "📋 Target Details:"
echo "   • Target ID: ${TARGET_ID:-unknown}"
echo "   • Target ARN: ${TARGET_ARN:-unknown}"
echo "   • Target Name: $TARGET_NAME"
echo "   • Lambda Function: $(basename "$LAMBDA_FUNCTION_ARN")"
echo "   • Tools Available: ${TOOL_COUNT:-unknown} tools"
echo ""
echo "📋 What was created:"
echo "   • AgentCore Gateway with OAuth2 JWT authorization"
echo "   • MCP target connected to Lambda function"
echo "   • Tool schemas for 20+ AWS services"
echo "   • Configuration updated with gateway details"
echo ""
echo "🔧 Available Tools:"
echo "   • Basic: hello_world, get_time"
echo "   • AWS Services: EC2, S3, Lambda, RDS, CloudFormation, IAM"
echo "   • AWS Services: ECS, EKS, SNS, SQS, DynamoDB, Route53"
echo "   • AWS Services: API Gateway, SES, CloudWatch, Cost Explorer"
echo "   • AWS Services: Bedrock, SageMaker"
echo ""
echo "🚀 Gateway is ready for MCP tool calls!"
echo "   Use the gateway URL in your AgentCore agents"
echo "   Tools accept natural language queries for AWS operations"
echo ""
echo "🚀 Next Steps:"
echo "   1. Run ./05-deploy-diy.sh to deploy DIY agent runtime"
echo "   2. Run ./06-deploy-sdk.sh to deploy SDK agent runtime"
echo "   3. Agents will use the gateway to access AWS tools"