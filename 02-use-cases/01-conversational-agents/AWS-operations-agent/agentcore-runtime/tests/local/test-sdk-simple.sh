#!/bin/bash

# Simple SDK Agent test with AWS credentials
echo "🧪 Simple SDK Agent test with AWS credentials..."

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

# Stop any existing container
docker stop test-sdk-simple 2>/dev/null || true
docker rm test-sdk-simple 2>/dev/null || true

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
echo "🚀 Starting SDK agent with AWS credentials..."
docker run -d \
  --name test-sdk-simple \
  -p 8081:8080 \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
  agentcore-sdk-agent:latest

# Wait for startup
echo "⏳ Waiting for agent to start..."
sleep 8

# Check if container is running
if ! docker ps | grep -q test-sdk-simple; then
    echo "❌ Container failed to start. Checking logs..."
    docker logs test-sdk-simple
    exit 1
fi

# Test ping endpoint first
echo "🏓 Testing ping endpoint..."
ping_response=$(curl -s http://localhost:8081/ping)
if [[ $ping_response == *"healthy"* ]]; then
    echo "✅ Ping successful: $ping_response"
else
    echo "❌ Ping failed: $ping_response"
    echo "📋 Container logs:"
    docker logs test-sdk-simple | tail -20
    exit 1
fi

# Test simple prompt that should work with local tools
echo ""
echo "🧪 Testing with simple time request:"
echo "================================"

# Create a simple test payload for SDK agent (BedrockAgentCoreApp format)
cat > /tmp/test_sdk_time_request.json << 'EOF'
{
  "prompt": "What is the current time?",
  "session_id": "test-time-123",
  "actor_id": "user"
}
EOF

echo "Request payload:"
cat /tmp/test_sdk_time_request.json
echo ""

# SDK agent uses /invocations endpoint but might have different response format
echo "Response:"
response=$(curl -s -X POST http://localhost:8081/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_sdk_time_request.json)

echo "$response"

echo ""
echo ""
echo "🧪 Testing with basic tool usage:"
echo "================================"

cat > /tmp/test_sdk_tool_request.json << 'EOF'
{
  "prompt": "Please use the get_current_time tool to show me the time, then echo back the message 'SDK Agent is working!'",
  "session_id": "test-tool-123",
  "actor_id": "user"  
}
EOF

echo "Request payload:"
cat /tmp/test_sdk_tool_request.json
echo ""

echo "Response:"
response=$(curl -s -X POST http://localhost:8081/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_sdk_tool_request.json)

echo "$response"

echo ""
echo ""
echo "📋 Container startup logs:"
echo "================================"
docker logs test-sdk-simple | head -30

echo ""
echo "📋 Recent container logs:"
echo "================================"
docker logs test-sdk-simple | tail -20

echo ""
echo "🎉 Simple testing complete!"
echo "================================"
echo "SDK Agent container details:"
echo "  Container: test-sdk-simple"
echo "  Port: 8081"
echo "  Endpoint: http://localhost:8081"
echo ""
echo "To view full container logs:"
echo "  docker logs test-sdk-simple"
echo ""
echo "To stop the test container:"
echo "  docker stop test-sdk-simple && docker rm test-sdk-simple"