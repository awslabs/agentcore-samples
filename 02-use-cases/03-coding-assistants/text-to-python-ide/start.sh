#!/bin/bash

# Integrated Start Script for AgentCore Code Interpreter
# Includes automatic setup and AWS resource provisioning

echo "🚀 AgentCore Code Interpreter - Starting Application"
echo "============================================================"

# Function to check if setup is needed
check_setup_needed() {
    local setup_needed=false

    # Check virtual environment
    if [ ! -d "venv" ]; then
        echo "📦 Virtual environment not found"
        setup_needed=true
    fi

    # Check Python dependencies
    if [ -d "venv" ]; then
        source venv/bin/activate
        if ! python -c "import strands, bedrock_agentcore, fastapi" 2>/dev/null; then
            echo "📦 Python dependencies missing"
            setup_needed=true
        fi
        deactivate 2>/dev/null || true
    fi

    # Check frontend dependencies
    if [ ! -d "frontend/node_modules" ]; then
        echo "📦 Frontend dependencies not found"
        setup_needed=true
    fi

    # Check .env file
    if [ ! -f ".env" ]; then
        echo "⚙️  Configuration file (.env) not found"
        setup_needed=true
    fi

    if [ "$setup_needed" = true ]; then
        return 0  # Setup needed
    else
        return 1  # Setup not needed
    fi
}

# Function to run setup
run_setup() {
    echo "🔧 Running automatic setup..."

    # Check if Python is installed
    if ! command -v python3 &> /dev/null; then
        echo "❌ Python 3 is required but not installed. Please install Python 3.8 or higher."
        exit 1
    fi

    # Check if Node.js is installed
    if ! command -v node &> /dev/null; then
        echo "❌ Node.js is required but not installed. Please install Node.js 16 or higher."
        exit 1
    fi

    # Create virtual environment for Python backend
    if [ ! -d "venv" ]; then
        echo "📦 Creating Python virtual environment..."
        python3 -m venv venv
    fi

    source venv/bin/activate

    # Install Python dependencies
    echo "📦 Installing Python dependencies..."
    pip install --upgrade pip -q
    pip install -r requirements.txt -q

    # Install Node.js dependencies for frontend
    if [ ! -d "frontend/node_modules" ]; then
        echo "📦 Installing Node.js dependencies..."
        cd frontend
        npm install --silent
        cd ..
    fi

    # Create .env file if it doesn't exist
    if [ ! -f .env ]; then
        echo "⚙️  Creating .env file from template..."
        if [ -f .env.example ]; then
            cp .env.example .env
        else
            cat > .env << EOF
# AWS Configuration
AWS_PROFILE=default
AWS_REGION=us-east-1

# Application Configuration
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
REACT_APP_API_URL=http://localhost:8000
EOF
        fi
    fi

    deactivate
    echo "✅ Local setup completed"
}

# Function to provision AWS resources
provision_aws_resources() {
    echo ""
    echo "☁️  Provisioning AWS resources..."
    echo "------------------------------------------------------------"

    source venv/bin/activate

    # Export .env values so child processes use the correct region/profile
    if [ -f .env ]; then
        export $(grep -v '^#' .env | grep -v '^$' | grep '=' | sed 's/=$/=/' | xargs)
    fi

    # Verify AWS credentials work
    echo "🔐 Verifying AWS credentials..."
    if ! python -c "
import boto3, os
session = boto3.Session(profile_name=os.getenv('AWS_PROFILE', 'default'), region_name=os.getenv('AWS_REGION', 'us-east-1'))
sts = session.client('sts')
identity = sts.get_caller_identity()
print(f\"   Account: {identity['Account']}\")
print(f\"   User/Role: {identity['Arn'].split('/')[-1]}\")
print(f\"   Region: {os.getenv('AWS_REGION', 'us-east-1')}\")
" 2>/dev/null; then
        echo "❌ AWS credentials are not configured or invalid."
        echo "   Please configure your AWS credentials (aws configure) and try again."
        exit 1
    fi
    echo "✅ AWS credentials verified"

    # 0. Ensure IAM execution role exists (needed by both Memory and Runtime)
    echo ""
    echo "🔧 Ensuring IAM execution role exists..."
    python -c "
import boto3, json, os
session = boto3.Session(profile_name=os.getenv('AWS_PROFILE', 'default'), region_name=os.getenv('AWS_REGION', 'us-east-1'))
iam = session.client('iam')
role_name = 'AgentCoreTextToPythonIDERole'
try:
    iam.get_role(RoleName=role_name)
    print(f'✅ Role exists: {role_name}')
except iam.exceptions.NoSuchEntityException:
    print(f'🔧 Creating role: {role_name}...')
    trust = json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'bedrock-agentcore.amazonaws.com'},'Action':'sts:AssumeRole'}]})
    iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=trust, Description='Execution role for AgentCore Text-to-Python IDE')
    for p in ['arn:aws:iam::aws:policy/AmazonBedrockFullAccess','arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess','arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly']:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=p)
    import time; time.sleep(10)
    print(f'✅ Role created: {role_name}')
# Ensure all policies are attached
attached = {p['PolicyArn'] for p in iam.list_attached_role_policies(RoleName=role_name).get('AttachedPolicies',[])}
for p in ['arn:aws:iam::aws:policy/AmazonBedrockFullAccess','arn:aws:iam::aws:policy/BedrockAgentCoreFullAccess','arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly']:
    if p not in attached:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=p)
        print(f'   Attached: {p.split(\"/\")[-1]}')
"

    # 1. Create Guardrail (if not already created)
    if [ ! -f "guardrail_info.json" ]; then
        echo ""
        echo "🛡️  Creating Bedrock Guardrail..."
        python setup_guardrails.py
        if [ $? -ne 0 ]; then
            echo "⚠️  Guardrail creation failed (non-critical, continuing without guardrails)"
        fi
    else
        echo "✅ Guardrail already provisioned (guardrail_info.json exists)"
    fi

    # 2. Create Memory (if not already created)
    if [ ! -f "memory_info.json" ]; then
        echo ""
        echo "🧠 Creating AgentCore Memory..."
        python setup_memory.py
        if [ $? -ne 0 ]; then
            echo "⚠️  Memory creation failed (non-critical, continuing without persistent memory)"
        fi
    else
        echo "✅ Memory already provisioned (memory_info.json exists)"
    fi

    # 3. Deploy Runtime (requires Docker or Finch)
    CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"
    export CONTAINER_ENGINE
    if [ ! -f "runtime_info.json" ]; then
        if ! command -v "$CONTAINER_ENGINE" &> /dev/null || ! "$CONTAINER_ENGINE" info &> /dev/null; then
            echo ""
            echo "❌ $CONTAINER_ENGINE is required but not running. Please start Docker Desktop (or Finch) and try again."
            echo "   Tip: export CONTAINER_ENGINE=finch to use Finch instead of Docker."
            exit 1
        fi
        echo ""
        echo "🚀 Deploying AgentCore Runtime (this may take a few minutes)..."
        python deploy_runtime.py
        if [ $? -ne 0 ]; then
            echo "❌ Runtime deployment failed."
            exit 1
        fi
    else
        echo "✅ Runtime already deployed (runtime_info.json exists)"
    fi

    # 3. Update .env with provisioned resource IDs
    echo ""
    echo "📝 Updating .env with resource IDs..."
    update_env_with_resources

    deactivate
    echo ""
    echo "✅ AWS resource provisioning completed"
    echo "------------------------------------------------------------"
}

# Function to update .env file with provisioned resource IDs
update_env_with_resources() {
    # Read guardrail info if available
    if [ -f "guardrail_info.json" ]; then
        GUARDRAIL_ID=$(python -c "import json; print(json.load(open('guardrail_info.json')).get('guardrail_id', ''))")
        GUARDRAIL_VERSION=$(python -c "import json; print(json.load(open('guardrail_info.json')).get('guardrail_version', ''))")

        if [ -n "$GUARDRAIL_ID" ]; then
            # Update or add guardrail ID in .env
            if grep -q "^BEDROCK_GUARDRAIL_ID=" .env; then
                sed -i '' "s|^BEDROCK_GUARDRAIL_ID=.*|BEDROCK_GUARDRAIL_ID=${GUARDRAIL_ID}|" .env
            else
                echo "BEDROCK_GUARDRAIL_ID=${GUARDRAIL_ID}" >> .env
            fi

            if grep -q "^BEDROCK_GUARDRAIL_VERSION=" .env; then
                sed -i '' "s|^BEDROCK_GUARDRAIL_VERSION=.*|BEDROCK_GUARDRAIL_VERSION=${GUARDRAIL_VERSION}|" .env
            else
                echo "BEDROCK_GUARDRAIL_VERSION=${GUARDRAIL_VERSION}" >> .env
            fi
            echo "   ✅ Guardrail: ${GUARDRAIL_ID} (v${GUARDRAIL_VERSION})"
        fi
    fi

    # Read memory info if available
    if [ -f "memory_info.json" ]; then
        MEMORY_ID=$(python -c "import json; print(json.load(open('memory_info.json')).get('memory_id', ''))")

        if [ -n "$MEMORY_ID" ]; then
            if grep -q "^AGENTCORE_MEMORY_ID=" .env; then
                sed -i '' "s|^AGENTCORE_MEMORY_ID=.*|AGENTCORE_MEMORY_ID=${MEMORY_ID}|" .env
            else
                echo "AGENTCORE_MEMORY_ID=${MEMORY_ID}" >> .env
            fi
            echo "   ✅ Memory: ${MEMORY_ID}"
        fi
    fi

    # Read runtime info if available
    if [ -f "runtime_info.json" ]; then
        RUNTIME_ARN=$(python -c "import json; print(json.load(open('runtime_info.json')).get('runtime_arn', ''))")

        if [ -n "$RUNTIME_ARN" ]; then
            if grep -q "^AGENTCORE_RUNTIME_ARN=" .env; then
                sed -i '' "s|^AGENTCORE_RUNTIME_ARN=.*|AGENTCORE_RUNTIME_ARN=${RUNTIME_ARN}|" .env
            else
                echo "AGENTCORE_RUNTIME_ARN=${RUNTIME_ARN}" >> .env
            fi
            echo "   ✅ Runtime: ${RUNTIME_ARN}"
        fi
    fi
}

# Function to check if backend is ready
check_backend() {
    local max_attempts=30
    local attempt=1

    echo "🔍 Waiting for backend to be ready..."

    while [ $attempt -le $max_attempts ]; do
        if curl -s http://localhost:8000/health > /dev/null 2>&1; then
            echo "✅ Backend is ready!"
            return 0
        fi

        if [ $attempt -eq 1 ]; then
            echo "⏳ Backend starting up..."
        elif [ $((attempt % 5)) -eq 0 ]; then
            echo "⏳ Still waiting... (${attempt}s)"
        fi

        sleep 2
        attempt=$((attempt + 1))
    done

    echo "❌ Backend failed to start after 60 seconds"
    echo "🔧 Check logs: tail -f backend.log"
    return 1
}

# Function to start backend
start_backend() {
    echo "🚀 Starting backend server..."

    # Kill any existing backend processes
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
    sleep 2

    # Start FastAPI backend (frontend REST API)
    (
        source venv/bin/activate
        cd backend

        if [ ! -f "main.py" ]; then
            echo "❌ backend/main.py not found"
            exit 1
        fi

        python main.py 2>&1 | tee ../backend.log &
        BACKEND_PID=$!
        echo $BACKEND_PID > ../backend.pid
        echo "📝 FastAPI backend started with PID: $BACKEND_PID (port 8000)"
    )

    # Start AgentCore Runtime (port 8080)
    (
        source venv/bin/activate
        cd backend

        if [ ! -f "agent_runtime.py" ]; then
            echo "⚠️  agent_runtime.py not found, skipping AgentCore Runtime"
        else
            python agent_runtime.py 2>&1 | tee ../agentcore_runtime.log &
            RUNTIME_PID=$!
            echo $RUNTIME_PID > ../agentcore_runtime.pid
            echo "📝 AgentCore Runtime started with PID: $RUNTIME_PID (port 8080)"
        fi
    )
}

# Function to start frontend
start_frontend() {
    echo "🚀 Starting frontend server..."

    # Kill any existing frontend processes
    lsof -ti:3000 | xargs kill -9 2>/dev/null || true
    sleep 2

    cd frontend

    # Check if package.json exists
    if [ ! -f "package.json" ]; then
        echo "❌ frontend/package.json not found"
        exit 1
    fi

    # Start the frontend
    npm start 2>&1 | tee ../frontend.log &
    FRONTEND_PID=$!
    echo $FRONTEND_PID > ../frontend.pid
    echo "📝 Frontend started with PID: $FRONTEND_PID"
    cd ..
}

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down servers..."

    # Kill FastAPI backend
    if [ -f backend.pid ]; then
        BACKEND_PID=$(cat backend.pid)
        kill $BACKEND_PID 2>/dev/null || true
        rm -f backend.pid
    fi

    # Kill AgentCore Runtime
    if [ -f agentcore_runtime.pid ]; then
        RUNTIME_PID=$(cat agentcore_runtime.pid)
        kill $RUNTIME_PID 2>/dev/null || true
        rm -f agentcore_runtime.pid
    fi

    # Kill frontend
    if [ -f frontend.pid ]; then
        FRONTEND_PID=$(cat frontend.pid)
        kill $FRONTEND_PID 2>/dev/null || true
        rm -f frontend.pid
    fi

    # Kill any remaining processes on ports
    lsof -ti:8000 | xargs kill -9 2>/dev/null || true
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
    lsof -ti:3000 | xargs kill -9 2>/dev/null || true

    echo "✅ Cleanup completed"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Main execution
main() {
    # Check if setup is needed and run it
    if check_setup_needed; then
        echo "🔧 Setup required. Running automatic setup..."
        run_setup
        echo ""
    fi

    # Provision AWS resources (guardrail, memory, runtime) if not already done
    provision_aws_resources

    echo ""
    echo "✅ Setup complete — all resources are ready."
    echo "============================================================"
    echo ""

    # Start backend
    start_backend

    # Wait for backend to be ready
    if ! check_backend; then
        echo "❌ Cannot start frontend without backend"
        cleanup
        exit 1
    fi

    # Start frontend
    start_frontend

    echo ""
    echo "🎉 Application started successfully!"
    echo "============================================================"
    echo "📊 FastAPI Backend:       http://localhost:8000"
    echo "🤖 AgentCore Runtime:     http://localhost:8080"
    echo "🌐 Frontend:              http://localhost:3000"
    echo ""
    echo "📋 Logs:"
    echo "   FastAPI Backend:   tail -f backend.log"
    echo "   AgentCore Runtime: tail -f agentcore_runtime.log"
    echo "   Frontend:          tail -f frontend.log"
    echo ""
    echo "Press Ctrl+C to stop the application"
    echo ""

    # Wait for user interrupt
    while true; do
        sleep 1
    done
}

# Run main function
main
