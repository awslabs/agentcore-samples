#!/bin/bash

# Manual start script for troubleshooting

echo "Manual Start - AgentCore Code Interpreter"
echo "=" * 50

# Step 1: Check environment
echo "1. Checking environment..."
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found. Run: ./setup.sh"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "❌ .env file not found. Copy from .env.example and configure."
    exit 1
fi

echo "✅ Environment files found"

# Step 2: Activate virtual environment
echo ""
echo "2. Activating virtual environment..."
source venv/bin/activate
echo "✅ Virtual environment activated"

# Step 3: Run diagnostics
echo ""
echo "3. Running diagnostics..."
python diagnose_backend.py
if [ $? -ne 0 ]; then
    echo "❌ Diagnostics failed. Fix issues above."
    exit 1
fi

# Step 4: Start backend manually
echo ""
echo "4. Starting backend (manual mode)..."
echo "📝 Backend will run in foreground. Open another terminal for frontend."
echo "🔗 Backend URL: http://localhost:8000"
echo "🔗 Health Check: http://localhost:8000/health"
echo ""
echo "⏹ Press Ctrl+C to stop backend"
echo ""

cd backend
python main.py
