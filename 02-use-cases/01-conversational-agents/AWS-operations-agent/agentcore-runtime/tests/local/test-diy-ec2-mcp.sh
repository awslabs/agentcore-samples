#!/bin/bash

# Test DIY Agent with EC2 listing via MCP Gateway
echo "🧪 Testing DIY Agent → MCP Gateway → Lambda Tool → EC2 (End-to-End)"

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
docker stop test-diy-ec2-mcp 2>/dev/null || true
docker rm test-diy-ec2-mcp 2>/dev/null || true

# Build fresh image with current configuration
echo "🔨 Building fresh DIY agent image with current configuration..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"
DEPLOYMENT_DIR="$PROJECT_ROOT/agentcore-runtime/deployment"

cd "$DEPLOYMENT_DIR"
docker build --platform linux/arm64 -t agentcore-diy-agent:latest -f Dockerfile.diy ../../ > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "✅ Fresh image built with latest configuration"
else
    echo "❌ Failed to build image"
    exit 1
fi

# Run container with AWS credentials
echo "🚀 Starting DIY agent with AWS credentials for MCP testing..."
docker run -d \
  --name test-diy-ec2-mcp \
  -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
  agentcore-diy-agent:latest

# Wait for startup
echo "⏳ Waiting for agent to start and initialize OAuth/MCP..."
sleep 10

# Check container logs for OAuth and MCP initialization
echo "📋 Checking agent initialization..."
docker logs test-diy-ec2-mcp | grep -E "(OAuth|MCP|Gateway|M2M|token)" | tail -10

echo ""
echo "🧪 Testing EC2 listing via MCP Gateway:"
echo "========================================"

# Create test request for EC2 instances
cat > /tmp/test_ec2_request.json << 'EOF'
{
  "prompt": "Can you list all currently running EC2 instances in my AWS account? Please show their instance IDs, types, and states. Use the MCP gateway tools to get this information from AWS.",
  "session_id": "test-ec2-mcp-123",
  "actor_id": "user"
}
EOF

echo "Request: List running EC2 instances via MCP gateway"
echo "Expected flow: DIY Agent → OAuth M2M Token → MCP Gateway → Lambda Tool → AWS EC2 API"
echo ""

# Make request with extended timeout for MCP calls
echo "Response (streaming):"
echo "===================="
timeout 60s curl -s -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_ec2_request.json | \
  while IFS= read -r line; do
    # Extract text content and tool calls
    if echo "$line" | grep -q '"type":"text_delta"'; then
      content=$(echo "$line" | sed 's/.*"content":"\([^"]*\)".*/\1/')
      printf "%s" "$content"
    elif echo "$line" | grep -q '"type":"tool_call"'; then
      tool_info=$(echo "$line" | sed 's/.*"content":"\([^"]*\)".*/\1/')
      echo ""
      echo "🔧 Tool Call: $tool_info"
    fi
  done

echo ""
echo ""
echo "📋 Full container logs (last 50 lines):"
echo "========================================"
docker logs test-diy-ec2-mcp | tail -50

echo ""
echo "🎯 Test Analysis:"
echo "================="
echo "✅ Check if M2M token was obtained successfully"
echo "✅ Check if MCP gateway connection was established" 
echo "✅ Check if Lambda tool was invoked"
echo "✅ Check if EC2 API call was successful"
echo "✅ Check if results were returned to agent"

echo ""
echo "🔍 To debug further:"
echo "  - Check container logs: docker logs test-diy-ec2-mcp"
echo "  - Check Lambda logs in CloudWatch: bac-mcp-function"
echo "  - Check Gateway logs in AgentCore console"
echo ""
echo "🧹 To clean up:"
echo "  docker stop test-diy-ec2-mcp && docker rm test-diy-ec2-mcp"