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

# Start the backend server
echo "🚀 Starting FastAPI backend server..."
echo "📍 Backend will be available at: http://localhost:8000"
echo "📖 API documentation at: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"

uvicorn app:app --host 0.0.0.0 --port 8000 --reload