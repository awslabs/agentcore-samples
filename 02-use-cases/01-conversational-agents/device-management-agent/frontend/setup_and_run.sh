#!/bin/bash

################################################################################
# Device Management Chat Application - Setup and Run Script
#
# This script provides automated setup and execution of the Device Management
# Chat Application frontend. It supports both Docker-based deployment and
# local Python development environments with automatic detection.
#
# DEPLOYMENT OPTIONS:
#   1. Docker Deployment (Recommended):
#      - Uses docker-compose for containerized deployment
#      - Automatic dependency management
#      - Consistent environment across systems
#      - Port 5001 exposed for web access
#
#   2. Local Python Deployment:
#      - Uses virtual environment (venv)
#      - Python 3.12 recommended (falls back to Python 3.x)
#      - Manual dependency installation via pip
#      - Development mode with auto-reload
#
# PREREQUISITES:
#   Docker Deployment:
#   - Docker installed and running
#   - docker-compose installed
#   - .env file configured (created from .env.example if missing)
#
#   Local Python Deployment:
#   - Python 3.8+ installed (3.12 recommended)
#   - pip package manager
#   - .env file configured
#
# REQUIRED ENVIRONMENT VARIABLES (.env file):
#   AWS_REGION              - AWS region for services
#   AGENT_ARN               - ARN of deployed agent runtime
#   COGNITO_DOMAIN          - Cognito domain for authentication (optional)
#   COGNITO_CLIENT_ID       - OAuth client ID (optional)
#   COGNITO_CLIENT_SECRET   - OAuth client secret (optional)
#   COGNITO_REDIRECT_URI    - OAuth redirect URI (optional)
#
# WHAT THIS SCRIPT DOES:
#   1. Detects available deployment method (Docker or Python)
#   2. Checks for .env file (creates from template if missing)
#   3. Docker: Builds and starts containers with docker-compose
#   4. Python: Creates venv, installs dependencies, runs uvicorn
#   5. Displays access URL and management commands
#
# DOCKER DEPLOYMENT:
#   Build and start:
#   - docker-compose up -d
#   
#   View logs:
#   - docker-compose logs -f
#   
#   Stop:
#   - docker-compose down
#
# LOCAL PYTHON DEPLOYMENT:
#   Virtual environment:
#   - Created in ./venv directory
#   - Activated automatically by script
#   
#   Server:
#   - uvicorn with auto-reload enabled
#   - Listens on 0.0.0.0:5001
#
# USAGE:
#   ./setup_and_run.sh
#
# ACCESS:
#   Web Application: http://localhost:5001
#   
#   Docker Logs: docker-compose logs -f
#   Docker Stop: docker-compose down
#
# EXIT CODES:
#   0 - Setup and run successful
#   1 - .env file missing (created, requires configuration)
#   1 - Python not found (local deployment)
#
# FEATURES:
#   - Automatic deployment method detection
#   - .env file creation from template
#   - Virtual environment management
#   - Dependency installation
#   - User-friendly status messages with emojis
#   - Clear next steps instructions
#
# NOTES:
#   - Docker deployment is preferred for production
#   - Local deployment is better for development
#   - Script exits after creating .env file (requires manual configuration)
#   - Python version detection with fallback support
#   - Supports both Python 3.12 and earlier versions
#
################################################################################

echo "🚀 Setting up Device Management Chat Application..."

# Function to check if Docker is available
check_docker() {
    if command -v docker &>/dev/null && command -v docker-compose &>/dev/null; then
        return 0
    else
        return 1
    fi
}

# Function to setup and run with Docker
run_with_docker() {
    echo "🐳 Using Docker deployment..."
    
    # Check if .env file exists
    if [ ! -f ".env" ]; then
        echo "📄 Creating .env file from template..."
        cp .env.example .env
        echo "⚠️  Please update the .env file with your configuration:"
        echo "   - AWS_REGION"
        echo "   - AGENT_ARN"
        echo "   - Cognito settings (optional)"
        echo ""
        echo "Then run this script again."
        exit 1
    fi
    
    # Build and run with Docker Compose
    echo "🔨 Building and starting containers..."
    docker-compose up -d
    
    echo "✅ Application started successfully!"
    echo "📱 Access the application at http://localhost:5001"
    echo "📋 View logs with: docker-compose logs -f"
    echo "🛑 Stop with: docker-compose down"
}

# Function to setup and run locally
run_locally() {
    echo "🐍 Using local Python deployment..."
    
    # Check if Python 3.12 is installed
    if command -v python3.12 &>/dev/null; then
        echo "✅ Python 3.12 found"
        PYTHON_CMD=python3.12
    elif command -v python3 &>/dev/null; then
        PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
        echo "🔍 Found Python $PYTHON_VERSION"
        if [[ "$PYTHON_VERSION" == 3.12* ]]; then
            echo "✅ Python 3.12 found"
            PYTHON_CMD=python3
        else
            echo "⚠️  Warning: Python 3.12 is recommended, but using $PYTHON_VERSION"
            PYTHON_CMD=python3
        fi
    else
        echo "❌ Python 3.12 not found. Please install Python 3.12"
        exit 1
    fi

    # Check if virtual environment exists
    if [ ! -d "venv" ]; then
        echo "🔧 Creating virtual environment..."
        $PYTHON_CMD -m venv venv
        echo "✅ Virtual environment created"
    fi

    # Activate virtual environment
    echo "🔌 Activating virtual environment..."
    source venv/bin/activate

    # Install dependencies
    echo "📦 Installing dependencies..."
    pip install -r requirements.txt

    # Check if .env file exists
    if [ ! -f ".env" ]; then
        echo "📄 Creating .env file from template..."
        cp .env.example .env
        echo "⚠️  Please update the .env file with your configuration:"
        echo "   - AWS_REGION"
        echo "   - AGENT_ARN"
        echo "   - Cognito settings (optional)"
        echo ""
        echo "Then run this script again."
        exit 1
    fi

    # Run the application
    echo "🚀 Starting the application..."
    echo "📱 Access the application at http://localhost:5001"
    uvicorn main:app --host 0.0.0.0 --port 5001 --reload
}

# Main execution
echo "Choose deployment method:"
echo "1. Docker (recommended)"
echo "2. Local Python"
echo ""

# Check if Docker is available and prefer it
if check_docker; then
    echo "🐳 Docker detected - using Docker deployment"
    run_with_docker
else
    echo "🐍 Docker not available - using local Python deployment"
    run_locally
fi
