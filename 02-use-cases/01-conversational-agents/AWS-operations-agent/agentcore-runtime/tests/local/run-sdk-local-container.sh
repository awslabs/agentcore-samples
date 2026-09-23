#!/bin/bash

# Local SDK Agent Test Runner
# This script builds and runs the SDK agent locally for testing

# Get the AgentCore project directory (go up 3 levels from tests/local to reach AgentCore root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTCORE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "🚀 Building and running local SDK agent..."
echo "📁 AgentCore root: $AGENTCORE_ROOT"
echo ""

# Verify we're in the right directory
if [[ ! -d "$AGENTCORE_ROOT/agentcore-runtime" ]]; then
    echo "❌ Error: agentcore-runtime directory not found at $AGENTCORE_ROOT"
    echo "   Expected structure: $AGENTCORE_ROOT/agentcore-runtime"
    exit 1
fi

# Change to AgentCore root for Docker build context
cd "$AGENTCORE_ROOT"

# Build the Docker image
echo "🔨 Building SDK Docker image..."
docker build -f ./agentcore-runtime/deployment/Dockerfile.sdk -t agentcore-sdk:latest .

if [[ $? -ne 0 ]]; then
    echo "❌ Docker build failed"
    exit 1
fi

echo "✅ Docker image built successfully"
echo ""

# Stop and remove existing container if it exists
echo "🧹 Cleaning up existing container..."
docker stop local-sdk-agent-test 2>/dev/null || true
docker rm local-sdk-agent-test 2>/dev/null || true

# Run the container
echo "🚀 Starting SDK agent container..."
docker run -d \
    --name local-sdk-agent-test \
    --network local-mcp-test \
    -p 8080:8080 \
    -e AWS_ACCESS_KEY_ID="$(aws configure get aws_access_key_id)" \
    -e AWS_SECRET_ACCESS_KEY="$(aws configure get aws_secret_access_key)" \
    -e AWS_SESSION_TOKEN="$(aws configure get aws_session_token)" \
    -e AWS_DEFAULT_REGION="$(aws configure get region)" \
    -e MCP_HOST="local-mcp-server-test" \
    agentcore-sdk:latest

if [[ $? -eq 0 ]]; then
    echo "✅ SDK agent container started successfully"
    echo ""
    echo "📋 Container Details:"
    echo "   • Name: local-sdk-agent-test"
    echo "   • Port: 8080"
    echo "   • Network: local-mcp-test"
    echo "   • Image: agentcore-sdk:latest"
    echo ""
    echo "🔗 Test the agent:"
    echo "   curl -X POST http://localhost:8080/invocations \\"
    echo "     -H 'Content-Type: application/json' \\"
    echo "     -d '{\"prompt\": \"Hello!\", \"session_id\": \"test\", \"actor_id\": \"user\"}'"
    echo ""
    echo "📊 Monitor logs:"
    echo "   docker logs -f local-sdk-agent-test"
    echo ""
    echo "🛑 Stop the container:"
    echo "   docker stop local-sdk-agent-test"
    echo ""
    echo "💡 Note: SDK agent uses BedrockAgentCoreApp framework"
    echo "   Response format may differ from DIY agent"
else
    echo "❌ Failed to start SDK agent container"
    exit 1
fi
