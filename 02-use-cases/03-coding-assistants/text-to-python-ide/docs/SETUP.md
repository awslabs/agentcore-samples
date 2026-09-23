# Setup Guide

## Prerequisites

Before setting up the AgentCore Code Interpreter, ensure you have:

- **Python 3.8+** with pip
- **Node.js 16+** with npm
- **AWS Account** with Bedrock access
- **AWS CLI** configured or credentials available

## Quick Setup

### 1. Initial Setup

```bash
# Navigate to the project directory
cd /path/to/strands-agents/agent-core/code-interpreter

# Run the automated setup script
./setup.sh
```

The setup script will:
- Create Python virtual environment
- Install backend dependencies
- Install frontend dependencies
- Create configuration files

### 2. AWS Configuration

Choose one of the following methods:

#### Option A: AWS Profile (Recommended)
```bash
# Configure AWS CLI with your profile
aws configure --profile your_profile_name

# Update .env file
echo "AWS_PROFILE=your_profile_name" >> .env
echo "AWS_REGION=us-east-1" >> .env
```

#### Option B: Access Keys
```bash
# Update .env file with your credentials
echo "AWS_ACCESS_KEY_ID=your_access_key" >> .env
echo "AWS_SECRET_ACCESS_KEY=your_secret_key" >> .env
echo "AWS_REGION=us-east-1" >> .env
```

### 3. AWS Permissions

Attach the following policy to your AWS user/role:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:ListFoundationModels"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:StartCodeInterpreterSession",
                "bedrock-agentcore:StopCodeInterpreterSession",
                "bedrock-agentcore:InvokeCodeInterpreter"
            ],
            "Resource": "*"
        }
    ]
}
```

Or use the managed policy: `BedrockAgentCoreFullAccess`

### 4. Verification

```bash
# Verify setup
python tests/verify_setup.py

# Run comprehensive tests
python tests/run_all_tests.py
```

### 5. Start Application

```bash
# Start both backend and frontend
./start.sh

# Access the application
# Frontend: http://localhost:3000
# Backend API: http://localhost:8000
```

## Manual Setup

If the automated setup fails, follow these manual steps:

### Backend Setup

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r backend/requirements.txt
```

### Frontend Setup

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Return to project root
cd ..
```

### Configuration

```bash
# Create .env file from template
cp .env.example .env

# Edit .env file with your settings
nano .env
```

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `AWS_PROFILE` | AWS profile name | Yes* | - |
| `AWS_ACCESS_KEY_ID` | AWS access key | Yes* | - |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | Yes* | - |
| `AWS_REGION` | AWS region | No | `us-east-1` |
| `BACKEND_HOST` | Backend host | No | `0.0.0.0` |
| `BACKEND_PORT` | Backend port | No | `8000` |
| `REACT_APP_API_URL` | Frontend API URL | No | `http://localhost:8000` |

*Either AWS_PROFILE or AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY is required

## Troubleshooting

### Common Issues

#### Virtual Environment Issues
```bash
# If venv creation fails
python3 -m venv venv

# If activation fails on Mac/Linux
chmod +x venv/bin/activate
source venv/bin/activate
```

#### Dependency Installation Issues
```bash
# Update pip first
pip install --upgrade pip

# Install with verbose output
pip install -r backend/requirements.txt -v

# For frontend issues
cd frontend
npm cache clean --force
npm install
```

#### AWS Configuration Issues
```bash
# Test AWS credentials
aws sts get-caller-identity

# Test Bedrock access
aws bedrock list-foundation-models --region us-east-1

# Test AgentCore access (requires BedrockAgentCoreFullAccess)
python -c "from bedrock_agentcore.tools.code_interpreter_client import code_session; print('AgentCore accessible')"
```

#### Port Conflicts
```bash
# Kill processes on ports 3000 and 8000
lsof -ti:3000 | xargs kill -9
lsof -ti:8000 | xargs kill -9
```

### Getting Help

1. **Check logs**: Look at `backend.log` and `frontend.log`
2. **Run diagnostics**: `python tests/verify_setup.py`
3. **Test components**: `python tests/run_all_tests.py`
4. **Verify AWS**: `aws bedrock list-foundation-models`

## Development Setup

For development work:

### Backend Development
```bash
# Activate virtual environment
source venv/bin/activate

# Start backend with auto-reload
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development
```bash
# Start frontend with hot reload
cd frontend
npm start
```

### Testing
```bash
# Run all tests
python tests/run_all_tests.py

# Run specific test
python -c "from tests.run_all_tests import TestRunner; runner = TestRunner(); runner.test_code_generation_api()"
```

## Next Steps

After successful setup:

1. **Start the application**: `./start.sh`
2. **Open browser**: Navigate to `http://localhost:3000`
3. **Generate code**: Try "Create a function to calculate fibonacci numbers"
4. **Execute code**: Click "Execute Code" to run in AgentCore sandbox
5. **Explore features**: Upload files, try interactive code, view session history

The application is now ready for use! 🚀
