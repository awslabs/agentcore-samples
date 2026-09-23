#!/bin/bash

# Deploy MCP Tool Lambda function using ZIP-based SAM (no Docker)
echo "🚀 Deploying MCP Tool Lambda function (ZIP-based, no Docker)..."

# Configuration - Get project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"  # Go up two levels to reach AgentCore root
RUNTIME_DIR="$(dirname "$SCRIPT_DIR")"  # agentcore-runtime directory
MCP_TOOL_DIR="${PROJECT_DIR}/mcp-tool-lambda"

# Load configuration from consolidated config files
CONFIG_DIR="${PROJECT_DIR}/config"

# Check if static config exists
if [[ ! -f "${CONFIG_DIR}/static-config.yaml" ]]; then
    echo "❌ Config file not found: ${CONFIG_DIR}/static-config.yaml"
    exit 1
fi

# Extract values from YAML (fallback method if yq not available)
get_yaml_value() {
    local key="$1"
    local file="$2"
    # Handle nested YAML keys with proper indentation
    grep "  $key:" "$file" | head -1 | sed 's/.*: *["'\'']*\([^"'\'']*\)["'\'']*$/\1/' | xargs
}

REGION=$(get_yaml_value "region" "${CONFIG_DIR}/static-config.yaml")
ACCOUNT_ID=$(get_yaml_value "account_id" "${CONFIG_DIR}/static-config.yaml")

if [[ -z "$REGION" || -z "$ACCOUNT_ID" ]]; then
    echo "❌ Failed to read region or account_id from static-config.yaml"
    exit 1
fi

STACK_NAME="bac-mcp-stack"

echo "📝 Configuration:"
echo "   Region: $REGION"
echo "   Account ID: $ACCOUNT_ID"
echo "   Stack Name: $STACK_NAME"
echo "   Deployment Type: ZIP-based (no Docker)"
echo "   MCP Tool Directory: $MCP_TOOL_DIR"
echo ""

# Check if MCP tool directory exists
if [[ ! -d "$MCP_TOOL_DIR" ]]; then
    echo "❌ MCP tool directory not found: $MCP_TOOL_DIR"
    exit 1
fi

# Function to setup virtual environment
setup_virtual_environment() {
    echo "🐍 Setting up Python virtual environment..."
    
    cd "$MCP_TOOL_DIR"
    
    # Check if .venv exists
    if [[ ! -d ".venv" ]]; then
        echo "   Creating new virtual environment..."
        python3 -m venv .venv
        if [[ $? -ne 0 ]]; then
            echo "❌ Failed to create virtual environment"
            exit 1
        fi
        echo "   ✅ Virtual environment created"
    else
        echo "   ✅ Virtual environment already exists"
    fi
    
    # Activate virtual environment
    echo "   Activating virtual environment..."
    source .venv/bin/activate
    if [[ $? -ne 0 ]]; then
        echo "❌ Failed to activate virtual environment"
        exit 1
    fi
    echo "   ✅ Virtual environment activated"
    
    # Verify Python version
    PYTHON_VERSION=$(python3 --version)
    echo "   Python version: $PYTHON_VERSION"
}

# Function to install dependencies
install_dependencies() {
    echo "📦 Installing Lambda dependencies..."
    
    cd "$MCP_TOOL_DIR"
    source .venv/bin/activate
    
    # Check if requirements.txt exists
    if [[ ! -f "lambda/requirements.txt" ]]; then
        echo "❌ Requirements file not found: lambda/requirements.txt"
        exit 1
    fi
    
    # Create packaging directory if it doesn't exist
    mkdir -p ./packaging/python
    
    # Install dependencies with Lambda-compatible settings
    echo "   Installing dependencies for Lambda runtime..."
    pip install -r lambda/requirements.txt \
        --python-version 3.12 \
        --platform manylinux2014_x86_64 \
        --target ./packaging/python \
        --only-binary=:all: \
        --upgrade
    
    if [[ $? -ne 0 ]]; then
        echo "❌ Failed to install dependencies"
        exit 1
    fi
    
    echo "   ✅ Dependencies installed successfully"
}

# Function to package Lambda function
package_lambda() {
    echo "📦 Packaging Lambda function..."
    
    cd "$MCP_TOOL_DIR"
    source .venv/bin/activate
    
    # Check if packaging script exists
    if [[ ! -f "package_for_lambda.py" ]]; then
        echo "❌ Packaging script not found: package_for_lambda.py"
        exit 1
    fi
    
    # Run packaging script
    python3 package_for_lambda.py
    if [[ $? -ne 0 ]]; then
        echo "❌ Failed to package Lambda function"
        exit 1
    fi
    
    echo "   ✅ Lambda function packaged successfully"
}

# Function to deploy with SAM
deploy_with_sam() {
    echo "🚀 Deploying with SAM..."
    
    cd "$MCP_TOOL_DIR"
    
    # Check if deployment script exists
    if [[ ! -f "deploy-mcp-tool-zip.sh" ]]; then
        echo "❌ Deployment script not found: deploy-mcp-tool-zip.sh"
        exit 1
    fi
    
    # Make sure deployment script is executable
    chmod +x deploy-mcp-tool-zip.sh
    
    # Run deployment script
    ./deploy-mcp-tool-zip.sh
    if [[ $? -ne 0 ]]; then
        echo "❌ SAM deployment failed"
        exit 1
    fi
    
    echo "   ✅ SAM deployment completed successfully"
}

# Main execution
echo "🔄 Starting complete ZIP-based deployment pipeline..."
echo ""

# Step 1: Setup virtual environment
setup_virtual_environment
echo ""

# Step 2: Install dependencies
install_dependencies
echo ""

# Step 3: Package Lambda function
package_lambda
echo ""

# Step 4: Deploy with SAM
deploy_with_sam
echo ""

echo "🎉 Complete MCP Tool Lambda Deployment Successful!"
echo "=================================================="
echo ""
echo "✅ Virtual environment: Created/verified"
echo "✅ Dependencies: Installed for Lambda runtime"
echo "✅ Lambda package: Created with all dependencies"
echo "✅ SAM deployment: Completed successfully"
echo ""
echo "🎯 Benefits of this deployment approach:"
echo "   • No Docker caching issues"
echo "   • Faster deployments"
echo "   • No Docker daemon required"
echo "   • Architecture-specific dependency handling"
echo "   • Automated virtual environment management"
echo "   • Complete dependency isolation"
echo ""
echo "📋 Next Steps:"
echo "   • Run ../05-create-gateway-targets.sh to create AgentCore Gateway"
echo "   • Test the Lambda function with MCP tools"
echo "   • Deploy DIY or SDK agents to use the MCP tools"
echo ""
echo "🔧 Troubleshooting:"
echo "   • Check CloudWatch logs: /aws/lambda/bac-mcp-tool"
echo "   • Verify IAM permissions for Cost Explorer and Budgets"
echo "   • Test individual tools with the Lambda function"
