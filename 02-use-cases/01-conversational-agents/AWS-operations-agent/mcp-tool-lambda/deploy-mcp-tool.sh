#!/bin/bash

# Deploy MCP Tool Lambda for Bedrock AgentCore Gateway testing
# Usage: ./deploy-mcp-tool.sh [aws-profile]

set -e

# Path to configuration files
PROJECT_ROOT="$(dirname "$(pwd)")"
STATIC_CONFIG_FILE="${PROJECT_ROOT}/config/static-config.yaml"
DYNAMIC_CONFIG_FILE="${PROJECT_ROOT}/config/dynamic-config.yaml"

# Check if static config exists
if [[ ! -f "$STATIC_CONFIG_FILE" ]]; then
    echo "❌ Config file not found: $STATIC_CONFIG_FILE"
    exit 1
fi

# Extract values from YAML (fallback method if yq not available)
get_yaml_value() {
    local key="$1"
    local file="$2"
    # Handle nested YAML keys with proper indentation
    grep "  $key:" "$file" | head -1 | sed 's/.*: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' | xargs
}

# Load configuration values
AWS_REGION=$(get_yaml_value "region" "$STATIC_CONFIG_FILE")
AWS_ACCOUNT=$(get_yaml_value "account_id" "$STATIC_CONFIG_FILE")
ECR_REPOSITORY=$(get_yaml_value "ecr_repository_name" "$STATIC_CONFIG_FILE")
STACK_NAME=$(get_yaml_value "stack_name" "$STATIC_CONFIG_FILE")
AWS_PROFILE=$(get_yaml_value "aws_profile" "$STATIC_CONFIG_FILE")

# Set defaults if not found in config
ECR_REPOSITORY=${ECR_REPOSITORY:-"bac-mcp-tool-repo"}
STACK_NAME=${STACK_NAME:-"bac-mcp-stack"}
AWS_PROFILE=${AWS_PROFILE:-${1}}  # Use script parameter if not in config

# Validate required values
if [[ -z "$AWS_REGION" || -z "$AWS_ACCOUNT" ]]; then
    echo "❌ Failed to read region or account_id from static-config.yaml"
    exit 1
fi

echo "🚀 Deploying MCP Tool Lambda for Bedrock AgentCore Gateway testing"
echo "=========================================================="
echo "AWS Profile: ${AWS_PROFILE:-default}"
echo "Stack Name: ${STACK_NAME}"
echo "ECR Repository: ${ECR_REPOSITORY}"

echo "📋 Configuration Values:"
echo "   AWS Region: ${AWS_REGION}"
echo "   AWS Account: ${AWS_ACCOUNT}"
echo ""

# Build Docker image with correct platform for Lambda (x86_64)
echo "🐳 Building Docker image for Lambda (x86_64 architecture)..."
cd lambda
docker build --platform linux/amd64 -t mcp-tool-lambda:latest .
cd ..

# Build ECR URI using configured values
ECR_URI="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"

# Check if ECR repository exists, create if not
echo "🔍 Checking if ECR repository exists..."
AWS_CLI_ARGS=""
if [[ -n "$AWS_PROFILE" ]]; then
    AWS_CLI_ARGS="--profile ${AWS_PROFILE}"
fi

if ! aws ecr describe-repositories --repository-names ${ECR_REPOSITORY} ${AWS_CLI_ARGS} --region ${AWS_REGION} &> /dev/null; then
    echo "📦 Creating ECR repository..."
    aws ecr create-repository --repository-name ${ECR_REPOSITORY} ${AWS_CLI_ARGS} --region ${AWS_REGION}
fi

# Login to ECR
echo "🔑 Logging in to ECR..."
aws ecr get-login-password ${AWS_CLI_ARGS} --region ${AWS_REGION} | docker login --username AWS --password-stdin ${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Tag and push Docker image
echo "🏷️  Tagging Docker image..."
docker tag mcp-tool-lambda:latest ${ECR_URI}:latest

echo "📤 Pushing Docker image to ECR..."
docker push ${ECR_URI}:latest

# Deploy SAM template
echo "🚀 Deploying SAM template..."
SAM_CLI_ARGS="${AWS_CLI_ARGS}"

sam deploy \
  --template-file mcp-tool-template.yaml \
  --stack-name ${STACK_NAME} \
  --image-repository ${ECR_URI} \
  ${SAM_CLI_ARGS} \
  --region ${AWS_REGION} \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# Get Lambda ARN
LAMBDA_ARN=$(aws cloudformation describe-stacks \
  --stack-name ${STACK_NAME} \
  ${AWS_CLI_ARGS} \
  --region ${AWS_REGION} \
  --query "Stacks[0].Outputs[?OutputKey=='MCPToolFunctionArn'].OutputValue" \
  --output text)

echo ""
echo "✅ Deployment completed successfully!"
echo "Lambda ARN: ${LAMBDA_ARN}"
echo ""

# Get Lambda Role ARN
LAMBDA_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name ${STACK_NAME} \
  ${AWS_CLI_ARGS} \
  --region ${AWS_REGION} \
  --query "Stacks[0].Outputs[?OutputKey=='MCPToolFunctionRoleArn'].OutputValue" \
  --output text)

# Get Gateway Execution Role ARN  
GATEWAY_EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
  --stack-name ${STACK_NAME} \
  ${AWS_CLI_ARGS} \
  --region ${AWS_REGION} \
  --query "Stacks[0].Outputs[?OutputKey=='BedrockAgentCoreGatewayExecutionRoleArn'].OutputValue" \
  --output text)

echo "Lambda Role ARN: ${LAMBDA_ROLE_ARN}"
echo ""

# Update dynamic configuration with deployment results
echo "📝 Updating dynamic configuration with deployment results..."

# Extract function name from ARN
FUNCTION_NAME=$(echo "$LAMBDA_ARN" | cut -d':' -f7)

# Check if dynamic config exists
if [[ ! -f "$DYNAMIC_CONFIG_FILE" ]]; then
    echo "❌ Dynamic config file not found: $DYNAMIC_CONFIG_FILE"
    exit 1
fi

# Use sed to update the mcp_lambda section (using | as delimiter to handle ARNs with /)
sed -i '' \
    -e "s|function_name: \"\"|function_name: \"$FUNCTION_NAME\"|" \
    -e "s|function_arn: \"\"|function_arn: \"$LAMBDA_ARN\"|" \
    -e "s|role_arn: \"\"|role_arn: \"$LAMBDA_ROLE_ARN\"|" \
    -e "s|stack_name: \"\"|stack_name: \"$STACK_NAME\"|" \
    -e "s|gateway_execution_role_arn: \"\"|gateway_execution_role_arn: \"$GATEWAY_EXECUTION_ROLE_ARN\"|" \
    -e "s|ecr_uri: \"\"|ecr_uri: \"$ECR_URI:latest\"|" \
    "$DYNAMIC_CONFIG_FILE"

echo "✅ Dynamic configuration updated successfully"

echo ""
echo "🎯 Next steps:"
echo "1. Create Bedrock AgentCore Gateway with this Lambda target:"
echo "   cd ../agentcore-runtime/deployment"
echo "   ./04-create-gateway-targets.sh"
echo "2. Test MCP protocol with the gateway!"
echo ""
