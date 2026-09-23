#!/bin/bash

# Exit on error
set -e

# Show usage if --help is passed
if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
    echo "Usage: $0 [ECR_REPO_NAME]"
    echo ""
    echo "Arguments:"
    echo "  ECR_REPO_NAME    Name for the ECR repository (default: sre_agent)"
    echo ""
    echo "Environment Variables:"
    echo "  LOCAL_BUILD      Set to 'true' for local container build without ECR push"
    echo "  PLATFORM         Set to 'x86_64' to build for local testing (default: arm64 for AgentCore)"
    echo "  DEBUG            Set to 'true' to enable debug mode in deployed agent"
    echo "  LLM_PROVIDER     Set to 'anthropic' or 'bedrock' (default: bedrock)"
    echo "  ANTHROPIC_API_KEY Required when using anthropic provider"
    echo ""
    echo "Examples:"
    echo "  # Deploy with default repo name"
    echo "  ./build_and_deploy.sh"
    echo ""
    echo "  # Deploy with custom repo name"
    echo "  ./build_and_deploy.sh my_custom_sre_agent"
    echo ""
    echo "  # Local build for testing"
    echo "  LOCAL_BUILD=true ./build_and_deploy.sh"
    echo ""
    echo "  # Deploy with debug and anthropic provider"
    echo "  DEBUG=true LLM_PROVIDER=anthropic ./build_and_deploy.sh my_sre_agent"
    exit 0
fi

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

# Configuration
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="${1:-sre_agent}"
RUNTIME_NAME="${RUNTIME_NAME:-$ECR_REPO_NAME}"
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ $? -ne 0 ] || [ -z "$AWS_ACCOUNT_ID" ] || [ "$AWS_ACCOUNT_ID" = "None" ]; then
    echo "❌ Failed to get AWS Account ID. Please check your AWS credentials and network connectivity."
    echo "Error: $AWS_ACCOUNT_ID"
    exit 1
fi

ECR_REPO_URI="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPO_NAME"

# Platform configuration (default to ARM64 for AgentCore)
PLATFORM="${PLATFORM:-arm64}"
LOCAL_BUILD="${LOCAL_BUILD:-false}"

# Get current caller identity and construct role ARN
CALLER_IDENTITY=$(aws sts get-caller-identity --output json)
CURRENT_ARN=$(echo $CALLER_IDENTITY | jq -r '.Arn')

# Extract role name from ARN and construct role ARN
# This handles both assumed-role and user scenarios
if [[ $CURRENT_ARN == *":assumed-role/"* ]]; then
    # Extract role name from assumed role ARN
    ROLE_NAME=$(echo $CURRENT_ARN | sed 's/.*:assumed-role\/\([^\/]*\).*/\1/')
    ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
else
    # Default role if not running with an assumed role
    ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/BedrockAgentCoreRole"
    echo "⚠️  Not running with an assumed role. Will use default role: $ROLE_ARN"
fi

# Allow override via environment variable
ROLE_ARN="${AGENT_ROLE_ARN:-$ROLE_ARN}"

echo "🔐 Logging in to Amazon ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# Create repository if it doesn't exist
echo "📦 Creating ECR repository if it doesn't exist..."
aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" || \
    aws ecr create-repository --repository-name "$ECR_REPO_NAME" --region "$AWS_REGION"

# Determine which Dockerfile to use and set up build environment
if [ "$PLATFORM" = "x86_64" ] || [ "$LOCAL_BUILD" = "true" ]; then
    echo "🏗️ Building Docker image for linux/amd64 (x86_64)..."
    DOCKERFILE="$PARENT_DIR/Dockerfile.x86_64"
    # Force platform to linux/amd64 for x86_64 builds
    docker build --platform linux/amd64 -f "$DOCKERFILE" -t "$ECR_REPO_NAME" "$PARENT_DIR"
else
    # Set up QEMU for ARM64 emulation
    echo "🔧 Setting up QEMU for ARM64 emulation..."
    docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
    
    # Build the Docker image for ARM64 (Dockerfile is at root level)
    echo "🏗️ Building Docker image for linux/arm64 (this may take longer due to emulation)..."
    DOCKERFILE="$PARENT_DIR/Dockerfile"
    # Explicitly set platform for ARM64
    DOCKER_BUILDKIT=0 docker build --platform linux/arm64 -f "$DOCKERFILE" -t "$ECR_REPO_NAME" "$PARENT_DIR"
fi

# For local builds, skip ECR push and deployment
if [ "$LOCAL_BUILD" = "true" ]; then
    echo "✅ Successfully built local image: $ECR_REPO_NAME:latest"
    echo ""
    echo "📝 To run the container locally:"
    echo "docker run -p 8080:8080 --env-file $PARENT_DIR/sre_agent/.env $ECR_REPO_NAME:latest"
    echo ""
    echo "Or with AWS credentials (bedrock provider - default):"
    echo "docker run -p 8080:8080 -v ~/.aws:/root/.aws:ro -e AWS_PROFILE=default -e GATEWAY_ACCESS_TOKEN=\$GATEWAY_ACCESS_TOKEN $ECR_REPO_NAME:latest"
    echo ""
    echo "Or with Anthropic provider:"
    echo "docker run -p 8080:8080 -e LLM_PROVIDER=anthropic -e ANTHROPIC_API_KEY=\$ANTHROPIC_API_KEY -e GATEWAY_ACCESS_TOKEN=\$GATEWAY_ACCESS_TOKEN $ECR_REPO_NAME:latest"
    exit 0
fi

# Tag the image
echo "🏷️ Tagging image..."
docker tag "$ECR_REPO_NAME":latest "$ECR_REPO_URI":latest

# Push the image to ECR
echo "⬆️ Pushing image to ECR..."
docker push "$ECR_REPO_URI":latest

echo "✅ Successfully built and pushed image to:"
echo "$ECR_REPO_URI:latest"

# Save the container URI to a file in the script directory
echo "💾 Saving container URI to .sre_agent_uri file..."
echo "$ECR_REPO_URI:latest" > "$SCRIPT_DIR/.sre_agent_uri"
echo "Container URI saved to $SCRIPT_DIR/.sre_agent_uri"

# Deploy the agent runtime
echo ""
echo "🚀 Deploying agent runtime..."
echo "Using role ARN: $ROLE_ARN"
echo "Using runtime name: $RUNTIME_NAME"
echo "Using region: $AWS_REGION"

# Check if .env file exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "❌ Error: .env file not found at $SCRIPT_DIR/.env"
    echo "Please create a .env file with ANTHROPIC_API_KEY and GATEWAY_ACCESS_TOKEN"
    echo "You can use .env.example as a template"
    exit 1
fi

echo ".env file found at $SCRIPT_DIR/.env"

# Deploy using the Python script
cd "$SCRIPT_DIR"

# Create a temporary file to capture output
TEMP_OUTPUT=$(mktemp)

# Log environment variables being passed
echo "🔧 Environment variables for deployment:"
echo "   DEBUG: ${DEBUG:-not set}"
echo "   LLM_PROVIDER: ${LLM_PROVIDER:-bedrock (default)}"
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "   ANTHROPIC_API_KEY: ***...${ANTHROPIC_API_KEY: -8}"
else
    echo "   ANTHROPIC_API_KEY: not set"
fi

# Change to parent directory to use uv
cd "$PARENT_DIR"

# Run the Python script and capture both return code and output
# Pass through DEBUG, LLM_PROVIDER, and ANTHROPIC_API_KEY environment variables
if DEBUG="$DEBUG" LLM_PROVIDER="${LLM_PROVIDER:-bedrock}" ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" uv run python deployment/deploy_agent_runtime.py \
    --container-uri "$ECR_REPO_URI:latest" \
    --role-arn "$ROLE_ARN" \
    --runtime-name "$RUNTIME_NAME" \
    --region "$AWS_REGION" \
    --force-recreate > "$TEMP_OUTPUT" 2>&1; then
    
    # Success - show output
    DEPLOY_OUTPUT=$(cat "$TEMP_OUTPUT")
    echo "$DEPLOY_OUTPUT"
else
    # Failure - show error output and exit
    echo "❌ Agent runtime deployment failed!"
    echo "Error output:"
    cat "$TEMP_OUTPUT"
    rm -f "$TEMP_OUTPUT"
    exit 1
fi

# Clean up temporary file
rm -f "$TEMP_OUTPUT"

echo ""
echo "🎉 Build and deployment complete!"