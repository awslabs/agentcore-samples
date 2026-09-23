#!/bin/bash

# Simple DIY Agent test with AWS credentials
echo "🧪 Simple DIY Agent test with AWS credentials..."

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
docker stop test-diy-simple 2>/dev/null || true
docker rm test-diy-simple 2>/dev/null || true

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
echo "🚀 Starting DIY agent with AWS credentials..."
docker run -d \
  --name test-diy-simple \
  -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
  -e AWS_SESSION_TOKEN="$AWS_SESSION_TOKEN" \
  -e AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
  agentcore-diy-agent:latest

# Wait for startup
echo "⏳ Waiting for agent to start..."
sleep 5

# Test simple prompt that should work with local tools
echo ""
echo "🧪 Testing with simple time request:"
echo "================================"

cat > /tmp/test_time_request.json << 'EOF'
{
  "prompt": "What is the current time?",
  "session_id": "test-time-123",
  "actor_id": "user"
}
EOF

# Extract just the text content from streaming response
echo "Response:"
curl -s -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_time_request.json | \
  grep '"type":"text_delta"' | \
  sed 's/.*"content":"\([^"]*\)".*/\1/' | \
  tr -d '\n'

echo ""
echo ""
echo "🧪 Testing with AWS environment variable check:"
echo "================================"

cat > /tmp/test_env_request.json << 'EOF'
{
  "prompt": "Can you tell me what AWS region environment variable is set? Use the get_current_time tool first to show you're working, then check if any AWS-related information is available to you.",
  "session_id": "test-env-123",
  "actor_id": "user"
}
EOF

echo "Response:"
curl -s -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d @/tmp/test_env_request.json | \
  grep '"type":"text_delta"' | \
  sed 's/.*"content":"\([^"]*\)".*/\1/' | \
  tr -d '\n'

echo ""
echo ""
echo "🎉 Simple testing complete!"
echo "================================"
echo "To view full container logs:"
echo "  docker logs test-diy-simple"
echo ""
echo "To stop the test container:"
echo "  docker stop test-diy-simple && docker rm test-diy-simple"