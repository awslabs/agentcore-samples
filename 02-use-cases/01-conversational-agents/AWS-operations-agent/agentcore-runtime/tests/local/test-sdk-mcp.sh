#!/bin/bash

# SDK Agent test with MCP Gateway integration
echo "🧪 Testing SDK Agent → MCP Gateway → Lambda Tool → AWS Services (End-to-End)"

# Get current AWS credentials
AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
AWS_SESSION_TOKEN=$(aws configure get aws_session_token)
AWS_DEFAULT_REGION=$(aws configure get region || echo "us-east-1")

# Check if we have credentials
if [ -z "$AWS_ACCESS_KEY_ID" ]; then
    echo "❌ No AWS credentials found. Please run 'aws configure' first."
    exit 1
fi

echo "✅ Found AWS credentials for account: $(aws sts get-caller-identity --query Account --output text)"
echo "🌍 Region: $AWS_DEFAULT_REGION"

# Stop any existing container
docker stop test-sdk-mcp 2>/dev/null || true
docker rm test-sdk-mcp 2>/dev/null || true

# Build fresh image with current configuration
echo "🔨 Building fresh SDK agent image with current configuration..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"
DEPLOYMENT_DIR="$PROJECT_ROOT/agentcore-runtime/deployment"

cd "$DEPLOYMENT_DIR"
docker build --platform linux/arm64 -t agentcore-sdk-agent:latest -f Dockerfile.sdk ../../ > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "✅ Fresh image built with latest configuration"
else
    echo "❌ Failed to build image"
    exit 1
fi

# Run container with AWS credentials
echo "🚀 Starting SDK agent with AWS credentials for MCP testing..."
docker run -d \
  --name test-sdk-mcp \
  -p 8081:8080 \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
  agentcore-sdk-agent:latest

# Wait for startup and OAuth/MCP initialization
echo "⏳ Waiting for agent to start and initialize OAuth/MCP..."
sleep 15

# Check if container is running
if ! docker ps | grep -q test-sdk-mcp; then
    echo "❌ Container failed to start. Checking logs..."
    docker logs test-sdk-mcp
    exit 1
fi

# Check container logs for OAuth and MCP initialization
echo "📋 Checking agent initialization..."
docker logs test-sdk-mcp | grep -E "(OAuth|MCP|Gateway|M2M|token|✅|❌)" | tail -15

# Test ping endpoint
echo ""
echo "🏓 Testing ping endpoint..."
ping_response=$(curl -s http://localhost:8081/ping)
if [[ $ping_response == *"healthy"* ]]; then
    echo "✅ Ping successful: $ping_response"
else
    echo "❌ Ping failed: $ping_response"
fi

echo ""
echo "🧪 Testing S3 bucket listing via MCP Gateway:"
echo "========================================"

# Create test request for S3 buckets (simpler than EC2)
cat > /tmp/test_sdk_s3_request.json << 'EOF'
{
  "prompt": "Can you list my S3 buckets? Please show their names and creation dates. Use the MCP gateway tools to get this information from AWS.",
  "session_id": "test-s3-mcp-123",
  "actor_id": "user"
}
EOF

echo "Request: List S3 buckets via MCP gateway"
echo "Expected flow: SDK Agent → OAuth M2M Token → MCP Gateway → Lambda Tool → AWS S3 API"
echo ""

# Make request with extended timeout for MCP calls
echo "Response (SDK Agent format):"
echo "===================="
timeout 90s curl -s -X POST http://localhost:8081/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_sdk_s3_request.json

echo ""
echo ""
echo "🧪 Testing EC2 instance listing via MCP Gateway:"
echo "========================================"

# Create test request for EC2 instances
cat > /tmp/test_sdk_ec2_request.json << 'EOF'
{
  "prompt": "Can you list all currently running EC2 instances in my AWS account? Please show their instance IDs, types, and states. Use the MCP gateway tools to get this information from AWS.",
  "session_id": "test-ec2-mcp-123", 
  "actor_id": "user"
}
EOF

echo "Request: List running EC2 instances via MCP gateway"
echo ""

echo "Response (SDK Agent format):"
echo "===================="
timeout 90s curl -s -X POST http://localhost:8081/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_sdk_ec2_request.json

echo ""
echo ""
echo "📋 Full container logs (startup):"
echo "========================================"
docker logs test-sdk-mcp | head -50

echo ""
echo "📋 Full container logs (recent):"
echo "========================================"
docker logs test-sdk-mcp | tail -50

echo ""
echo "🎯 Test Analysis:"
echo "================="
echo "✅ Check if M2M token was obtained successfully"
echo "✅ Check if MCP gateway connection was established" 
echo "✅ Check if Lambda tool was invoked"
echo "✅ Check if AWS API calls were successful"
echo "✅ Check if results were returned to agent"

echo ""
echo "🔍 To debug further:"
echo "  - Check container logs: docker logs test-sdk-mcp"
echo "  - Check Lambda logs in CloudWatch: bac-mcp-function"
echo "  - Check Gateway logs in AgentCore console"
echo ""
echo "🧹 To clean up:"
echo "  docker stop test-sdk-mcp && docker rm test-sdk-mcp"