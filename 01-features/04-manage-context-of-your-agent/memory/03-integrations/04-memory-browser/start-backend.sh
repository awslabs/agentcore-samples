#!/bin/bash

# Start AgentCore Memory Dashboard Backend
echo "🚀 Starting AgentCore Memory Dashboard Backend..."

# Check if we're in the right directory
if [ ! -f "backend/app.py" ]; then
    echo "❌ Error: backend/app.py not found. Please run this script from the agentcore-memory-dashboard directory."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "backend/venv" ]; then
    echo "📦 Creating Python virtual environment..."
    cd backend
    python3 -m venv venv
    cd ..
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source backend/venv/bin/activate

# Install dependencies
echo "📦 Installing Python dependencies..."
cd backend
pip install -r requirements.txt

# Check if bedrock-agentcore is available
echo "🔍 Checking AgentCore Memory SDK..."
python -c "
try:
    from bedrock_agentcore.memory import MemoryClient
    print('✅ bedrock-agentcore SDK is available')
except ImportError:
    print('⚠️  bedrock-agentcore SDK not found')
    print('   The backend will use mock data for development')
    print('   To install: pip install bedrock-agentcore')
"

# Start the backend server.
# Bind to localhost by default — this backend is an unauthenticated proxy to your
# AWS-credentialed memory store, so it must NOT be exposed on the network. Honor
# $BACKEND_HOST (the README/.env-documented knob) only if you deliberately set it.
BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"

echo "🚀 Starting FastAPI backend server..."
echo "📍 Backend will be available at: http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "📖 API documentation at: http://${BACKEND_HOST}:${BACKEND_PORT}/docs"
echo ""
echo "Press Ctrl+C to stop the server"

uvicorn app:app --host "${BACKEND_HOST}" --port "${BACKEND_PORT}" --reload