#!/bin/bash

# Deploy MCP Tool Lambda function using SAM with ZIP packaging (no Docker)
echo "🚀 Deploying MCP Tool Lambda function (ZIP-based, no Docker)..."

# Configuration - Get project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"  # Go up one level to reach AgentCore root
RUNTIME_DIR="${PROJECT_DIR}/agentcore-runtime"  # agentcore-runtime directory

# Load configuration from consolidated config files
CONFIG_DIR="${PROJECT_DIR}/config"

# Check if static config exists
if [[ ! -f "${CONFIG_DIR}/static-config.yaml" ]]; then
    echo "❌ Config file not found: ${CONFIG_DIR}/static-config.yaml"
    exit 1
fi

# Extract values from YAML (fallback method if yq not available)
get_yaml_value() {
    local key="$1"
    local file="$2"
    # Handle nested YAML keys with proper indentation
    grep "  $key:" "$file" | head -1 | sed 's/.*: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' | xargs
}

REGION=$(get_yaml_value "region" "${CONFIG_DIR}/static-config.yaml")
ACCOUNT_ID=$(get_yaml_value "account_id" "${CONFIG_DIR}/static-config.yaml")

if [[ -z "$REGION" || -z "$ACCOUNT_ID" ]]; then
    echo "❌ Failed to read region or account_id from static-config.yaml"
    exit 1
fi

STACK_NAME="bac-mcp-stack"

echo "📝 Configuration:"
echo "   Region: $REGION"
echo "   Account ID: $ACCOUNT_ID"
echo "   Stack Name: $STACK_NAME"
echo "   Deployment Type: ZIP-based (no Docker)"
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

# Check if SAM is installed
if ! command -v sam &> /dev/null; then
    echo "❌ SAM CLI is not installed. Please install SAM CLI:"
    echo "   https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
    exit 1
fi

echo "✅ SAM CLI found: $(sam --version)"

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3."
    exit 1
fi

echo "✅ Python found: $(python3 --version)"

# Change to MCP tool directory
cd "${SCRIPT_DIR}"

# Check if ZIP template exists
if [[ ! -f "mcp-tool-template-zip.yaml" ]]; then
    echo "❌ ZIP-based SAM template not found: mcp-tool-template-zip.yaml"
    exit 1
fi

echo "✅ ZIP-based SAM template found: mcp-tool-template-zip.yaml"

# Force fresh deployment by deleting existing stack
echo "🧹 Ensuring fresh deployment..."

# Check if stack exists and delete it for fresh deployment
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" &>/dev/null; then
    echo "   📦 Found existing CloudFormation stack: $STACK_NAME"
    echo "   🔄 Deleting existing stack for fresh deployment..."
    
    # Delete the CloudFormation stack
    echo "   🗑️  Deleting CloudFormation stack..."
    aws cloudformation delete-stack \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    
    # Wait for stack deletion to complete
    echo "   ⏳ Waiting for stack deletion to complete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name "$STACK_NAME" \
        --region "$REGION"
    
    echo "   ✅ Stack deleted successfully"
else
    echo "   ℹ️  No existing stack found, proceeding with fresh deployment"
fi

# Clean SAM build cache
echo "🧹 Cleaning SAM build cache..."
if [[ -d ".aws-sam" ]]; then
    rm -rf .aws-sam
    echo "   ✅ SAM build cache cleared"
else
    echo "   ℹ️  No SAM build cache to clear"
fi

# Package the Lambda function
echo "📦 Packaging Lambda function..."
if ! python3 package_for_lambda.py; then
    echo "❌ Lambda packaging failed"
    exit 1
fi

echo "✅ Lambda packaging completed"

# Build the SAM application
echo "🔨 Building SAM application..."
if ! sam build --template-file mcp-tool-template-zip.yaml --no-cached; then
    echo "❌ SAM build failed"
    exit 1
fi

echo "✅ SAM build completed"

# Deploy the SAM application
echo "📤 Deploying SAM application..."
if sam deploy \
    --template-file mcp-tool-template-zip.yaml \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --parameter-overrides "Environment=prod" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --resolve-s3 \
    --no-fail-on-empty-changeset; then
    echo "✅ SAM deployment completed"
else
    echo "❌ SAM deployment failed"
    exit 1
fi

# Get Lambda function ARN from CloudFormation stack outputs
echo "📋 Retrieving Lambda function details..."
FUNCTION_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='MCPToolFunctionArn'].OutputValue" \
    --output text)

FUNCTION_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='MCPToolFunctionName'].OutputValue" \
    --output text)

FUNCTION_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='MCPToolFunctionRoleArn'].OutputValue" \
    --output text)

GATEWAY_EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='BedrockAgentCoreGatewayExecutionRoleArn'].OutputValue" \
    --output text)

if [[ -z "$FUNCTION_ARN" || "$FUNCTION_ARN" == "None" ]]; then
    echo "❌ Failed to retrieve Lambda function ARN from CloudFormation stack"
    exit 1
fi

if [[ -z "$GATEWAY_EXECUTION_ROLE_ARN" || "$GATEWAY_EXECUTION_ROLE_ARN" == "None" ]]; then
    echo "❌ Failed to retrieve Gateway Execution Role ARN from CloudFormation stack"
    exit 1
fi

# Update dynamic configuration file with Lambda details
echo "📝 Updating dynamic configuration with Lambda details..."

# Update the mcp_lambda section in the dynamic configuration
DYNAMIC_CONFIG="${CONFIG_DIR}/dynamic-config.yaml"

# Check if dynamic config exists
if [[ ! -f "$DYNAMIC_CONFIG" ]]; then
    echo "❌ Dynamic config file not found: $DYNAMIC_CONFIG"
    exit 1
fi

# Use sed to update the mcp_lambda section (using | as delimiter to handle ARNs with /)
echo "   📝 Updating mcp_lambda section in dynamic-config.yaml..."

sed -i '' \
    -e "s|function_name: \"\"|function_name: \"$FUNCTION_NAME\"|" \
    -e "s|function_arn: \"\"|function_arn: \"$FUNCTION_ARN\"|" \
    -e "s|role_arn: \"\"|role_arn: \"$FUNCTION_ROLE_ARN\"|" \
    -e "s|stack_name: \"\"|stack_name: \"$STACK_NAME\"|" \
    -e "s|gateway_execution_role_arn: \"\"|gateway_execution_role_arn: \"$GATEWAY_EXECUTION_ROLE_ARN\"|" \
    "$DYNAMIC_CONFIG"

echo "✅ Configuration updated with Lambda details"

# Test the Lambda function
echo "🧪 Testing Lambda function..."
TEST_PAYLOAD='{"name": "AgentCore"}'

if aws lambda invoke \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --payload "$TEST_PAYLOAD" \
    --cli-binary-format raw-in-base64-out \
    /tmp/lambda-test-response.json > /dev/null; then
    
    echo "✅ Lambda function test successful"
    echo "   Response: $(cat /tmp/lambda-test-response.json)"
    rm -f /tmp/lambda-test-response.json
else
    echo "⚠️  Lambda function test failed (this might be expected if tool name extraction fails)"
fi

echo ""
echo "🎉 MCP Tool Lambda Deployment Complete!"
echo "======================================"
echo "✅ Lambda function deployed and configured (ZIP-based, no Docker)"
echo ""
echo "📋 Deployment Details:"
echo "   • Function Name: $FUNCTION_NAME"
echo "   • Function ARN: $FUNCTION_ARN"
echo "   • Lambda Function Role ARN: $FUNCTION_ROLE_ARN"
echo "   • Gateway Execution Role ARN: $GATEWAY_EXECUTION_ROLE_ARN"
echo "   • Stack Name: $STACK_NAME"
echo "   • Region: $REGION"
echo "   • Deployment Type: ZIP-based (no Docker caching issues)"
echo ""
echo "📋 What was deployed:"
echo "   • Lambda function with MCP tool handlers (ZIP package)"
echo "   • IAM role with Bedrock and AWS service permissions"
echo "   • CloudWatch log group for function logs"
echo "   • SAM-managed deployment infrastructure"
echo ""
echo "🚀 Next Steps:"
echo "   Run ./05-create-gateway-targets.sh to create AgentCore Gateway and targets"
echo "   The Lambda function is ready to handle MCP tool calls"
echo ""
echo "💡 Function Capabilities:"
echo "   • Basic tools: hello_world, get_time"
echo "   • AWS service tools: EC2, S3, Lambda, RDS, and 16 more services"
echo "   • Natural language query processing via Strands Agent"
echo ""
echo "🔧 Deployment Features:"
echo "   • ZIP-based deployment eliminates Docker caching issues"
echo "   • Architecture-specific dependency installation for Lambda x86_64"
echo "   • Automatic stack cleanup for fresh deployments"
echo "   • No Docker dependencies required"
echo "   • No API Gateway provisioned (Lambda only for MCP Gateway integration)"
