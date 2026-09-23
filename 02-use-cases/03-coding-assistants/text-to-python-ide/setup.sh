#!/bin/bash

echo "Setting up AgentCore Code Interpreter Application..."

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is required but not installed. Please install Python 3.8 or higher."
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "Node.js is required but not installed. Please install Node.js 16 or higher."
    exit 1
fi

# Create virtual environment for Python backend
echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Install Node.js dependencies for frontend
echo "Installing Node.js dependencies..."
cd frontend
npm install
cd ..

# Copy environment file
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "Please edit .env file with your AWS credentials and configuration."
    else
        echo "Creating basic .env file..."
        cat > .env << EOF
# AWS Configuration (choose one method)
AWS_PROFILE=default
AWS_REGION=us-east-1

# Application Configuration
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
REACT_APP_API_URL=http://localhost:8000
EOF
        echo "Basic .env file created. Please configure your AWS credentials."
    fi
fi

# Run setup verification
echo "Running setup verification..."
source venv/bin/activate
python tests/verify_setup.py

echo ""
echo "Setup complete!"
echo ""
echo "Architecture: Hybrid Strands + AgentCore"
echo "- Code Generation: Strands Agent with Claude Haiku 4.5"
echo "- Code Execution: AgentCore Agent with Code Interpreter Tool"
echo ""
echo "To start the application:"
echo "1. Quick start: ./start.sh"
echo "2. Manual start:"
echo "   - Backend: source venv/bin/activate && python backend/main.py"
echo "   - Frontend: cd frontend && npm start"
echo ""
echo "Make sure to configure your AWS credentials in the .env file."
