# SRE Agent Deployment Guide for Amazon Bedrock AgentCore Runtime

This guide walks you through the complete deployment process for the SRE Agent, from local testing to production deployment on Amazon Bedrock AgentCore Runtime.

## Prerequisites

- AWS CLI configured with appropriate permissions
- Docker installed and running
- UV package manager installed
- Python 3.12+
- Access to Amazon Bedrock AgentCore Runtime
- IAM role with `BedrockAgentCoreFullAccess` policy and appropriate trust policy (see [Authentication Setup](auth.md))

## Environment Configuration

The SRE Agent uses environment variables for configuration. These are read from `.env` files in the appropriate directories:

- **CLI Testing**: Environment variables are read from `sre_agent/.env`
- **Container Building**: Environment variables are read from `deployment/.env` 
- **Docker Platform**: Local builds use `Dockerfile.x86_64` (linux/amd64), AgentCore deployments use `Dockerfile` (linux/arm64)

### Required Environment Variables

Create the appropriate `.env` files with these variables:

**For sre_agent/.env (CLI testing and local container runs):**
```bash
GATEWAY_ACCESS_TOKEN=your_gateway_access_token
LLM_PROVIDER=bedrock
DEBUG=false
# If using Anthropic provider, also add:
# ANTHROPIC_API_KEY=sk-ant-your-key-here
```

**For deployment/.env (container building and deployment):**
```bash
GATEWAY_ACCESS_TOKEN=your_gateway_access_token
ANTHROPIC_API_KEY=sk-ant-your-key-here
# These can be overridden by environment variables during build/deploy
```

**Note**: When using `--env-file`, all required variables should be in the .env file. Use `-e` only to override specific variables from the .env file.

## Deployment Sequence

### Phase 1: Local Testing with CLI

First, test the SRE agent locally using the command-line interface to ensure it works correctly.

#### 1.1 Setup Environment

Create and configure your environment files:
```bash
# Setup CLI environment file
cp sre_agent/.env.example sre_agent/.env
# Edit sre_agent/.env with your configuration
```

**Note**: Environment variables can be overridden at runtime, but having .env files ensures consistent configuration.

#### 1.2 Test CLI with Bedrock (Default)

```bash
# Test with default Bedrock provider
uv run sre-agent --prompt "list the pods in my infrastructure"

# Test with debug output enabled
uv run sre-agent --prompt "list the pods in my infrastructure" --debug

# Test with specific provider
uv run sre-agent --prompt "list the pods in my infrastructure" --provider bedrock --debug
```

#### 1.3 Test CLI with Anthropic Provider

```bash
# Ensure ANTHROPIC_API_KEY is set in your .env file, then:
uv run sre-agent --prompt "list the pods in my infrastructure" --provider anthropic --debug
```

**Expected Output**: You should see the agent processing your request, routing to appropriate specialized agents, and returning infrastructure information.

### Phase 2: Local Container Testing

Once CLI testing is successful, build and test the agent as a container locally.

#### 2.1 Build Local Container

The build script accepts an optional ECR repository name and uses different Dockerfiles based on the target platform:

- **Local builds** (LOCAL_BUILD=true): Uses `Dockerfile.x86_64` for linux/amd64 platform
- **AgentCore builds** (default): Uses `Dockerfile` for linux/arm64 platform (required by AgentCore)

```bash
# Build container for local testing with custom name
LOCAL_BUILD=true ./deployment/build_and_deploy.sh my_custom_sre_agent

# View help for all options
./deployment/build_and_deploy.sh --help
```

#### 2.2 Test Local Container with Bedrock

Run the container locally with default Bedrock provider:
```bash
# Using .env file from sre_agent directory (recommended)
# Ensure LLM_PROVIDER=bedrock is set in sre_agent/.env
docker run -p 8080:8080 --env-file sre_agent/.env my_custom_sre_agent:latest

# Alternative: with explicit environment variables (if not using .env file)
docker run -p 8080:8080 \
  -v ~/.aws:/root/.aws:ro \
  -e AWS_PROFILE=default \
  -e GATEWAY_ACCESS_TOKEN=your_token \
  -e LLM_PROVIDER=bedrock \
  my_custom_sre_agent:latest

# With debug enabled (overrides DEBUG setting from .env file)
docker run -p 8080:8080 --env-file sre_agent/.env -e DEBUG=true my_custom_sre_agent:latest
```

**Note**: The container name matches the ECR repository name you specified during build.

#### 2.3 Test Local Container with Anthropic

```bash
# Using .env file (ensure LLM_PROVIDER=anthropic is set in sre_agent/.env)
docker run -p 8080:8080 --env-file sre_agent/.env my_custom_sre_agent:latest

# With debug enabled (override DEBUG setting from .env file)
docker run -p 8080:8080 \
  --env-file sre_agent/.env \
  -e DEBUG=true \
  my_custom_sre_agent:latest
```

**Note**: Ensure both `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` are set in your `sre_agent/.env` file when using the anthropic provider.

#### 2.4 Test Container with curl

Test the running container:
```bash
# Basic test
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "list the pods in my infrastructure"
    }
  }'

# Health check
curl http://localhost:8080/ping
```

**Expected Output**: The container should respond with JSON containing the agent's response.

### Phase 3: Amazon Bedrock AgentCore Runtime Deployment

Once local container testing is successful, deploy to AgentCore.

#### 3.1 Deploy to AgentCore with Bedrock

```bash
# Deploy with custom repository name and default settings (reads from deployment/.env)
./deployment/build_and_deploy.sh my_custom_sre_agent

# Deploy with debug enabled (environment variable override)
DEBUG=true ./deployment/build_and_deploy.sh my_custom_sre_agent

# Deploy with specific provider
LLM_PROVIDER=bedrock DEBUG=true ./deployment/build_and_deploy.sh my_custom_sre_agent
```

#### 3.2 Deploy to AgentCore with Anthropic

```bash
# Deploy with Anthropic provider (ensure ANTHROPIC_API_KEY is in deployment/.env)
LLM_PROVIDER=anthropic ./deployment/build_and_deploy.sh my_custom_sre_agent

# Deploy with Anthropic and debug enabled
DEBUG=true LLM_PROVIDER=anthropic ./deployment/build_and_deploy.sh my_custom_sre_agent

# Override API key via environment variable
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-your-key ./deployment/build_and_deploy.sh my_custom_sre_agent
```

**Build Script Usage:**
```bash
# View all available options
./deployment/build_and_deploy.sh --help

# The script accepts one optional argument: ECR repository name
# Default repository name is 'sre_agent'
# Note: Use underscores (_) instead of hyphens (-) in repository names
```

**Expected Output**: The script will build, push to ECR, and deploy to AgentCore Runtime.

#### 3.3 Test AgentCore Deployment

Test the deployed agent using the invoke script:
```bash
# Test deployed agent
uv run python deployment/invoke_agent_runtime.py \
  --prompt "list the pods in my infrastructure"

# Test with custom runtime ARN
uv run python deployment/invoke_agent_runtime.py \
  --prompt "list the pods in my infrastructure" \
  --runtime-arn "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/your-runtime-id"
```

## Environment Variables Reference

### Core Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `GATEWAY_ACCESS_TOKEN` | Gateway authentication token | - | Yes |
| `BACKEND_API_KEY` | Backend API key for credential provider | - | Yes (gateway setup) |
| `LLM_PROVIDER` | Language model provider | `bedrock` | No |
| `ANTHROPIC_API_KEY` | Anthropic API key | - | Only for anthropic provider |
| `DEBUG` | Enable debug logging and traces | `false` | No |

### AWS Configuration

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `AWS_REGION` | AWS region for deployment | `us-east-1` | No |
| `AWS_PROFILE` | AWS profile to use | - | No |
| `RUNTIME_NAME` | AgentCore runtime name | ECR repo name | No |

### Build Script Configuration

| Variable | Description | Default | Notes |
|----------|-------------|---------|-------|
| `LOCAL_BUILD` | Build for local testing only | `false` | Uses Dockerfile.x86_64 when true |
| `PLATFORM` | Target platform | `arm64` | AgentCore requires arm64, use x86_64 for local |
| `ECR_REPO_NAME` | ECR repository name | `sre_agent` | Can be passed as command line argument |

## Debug Mode Usage

### CLI Debug Mode
```bash
# Enable debug with --debug flag
uv run sre-agent --prompt "your query" --debug

# Or with environment variable
DEBUG=true uv run sre-agent --prompt "your query"
```

### Container Debug Mode
```bash
# Local container with debug (overrides DEBUG setting in .env file)
docker run -p 8080:8080 --env-file sre_agent/.env -e DEBUG=true my_custom_sre_agent:latest

# AgentCore deployment with debug
DEBUG=true ./deployment/build_and_deploy.sh my_custom_sre_agent
```

### Debug Output Examples

**Without Debug Mode:**
```
🤖 Multi-Agent System: Processing...
🧭 Supervisor: Routing to kubernetes_agent
🔧 Kubernetes Agent:
   💡 Full Response: Here are the pods in your infrastructure...
💬 Final Response: I found 5 pods running in your infrastructure...
```

**With Debug Mode:**
```
🤖 Multi-Agent System: Processing...

MCP tools loaded: 12
  - kubernetes-list-pods: List all pods in the cluster...
  - kubernetes-get-pod: Get details of a specific pod...

🧭 Supervisor: Routing to kubernetes_agent
🔧 Kubernetes Agent:
   🔍 DEBUG: agent_messages = 3
   📋 Found 3 trace messages:
      1. AIMessage: I'll help you list the pods...
   📞 Calling tools:
      kubernetes-list-pods(
        namespace=None
      ) [id: call_123]
   🛠️  kubernetes-list-pods [id: call_123]:
      {"pods": [...]}
   💡 Full Response: Here are the pods in your infrastructure...
💬 Final Response: I found 5 pods running in your infrastructure...
```

## Provider Configuration

### Using Amazon Bedrock (Default)
```bash
# CLI (reads from sre_agent/.env)
uv run sre-agent --provider bedrock --prompt "your query"

# Container (reads LLM_PROVIDER=bedrock from sre_agent/.env)
docker run -p 8080:8080 --env-file sre_agent/.env my_custom_sre_agent:latest

# Deployment (reads from deployment/.env, can override via environment variable)
LLM_PROVIDER=bedrock ./deployment/build_and_deploy.sh my_custom_sre_agent
```

### Using Anthropic Claude
```bash
# CLI (reads LLM_PROVIDER and ANTHROPIC_API_KEY from sre_agent/.env)
uv run sre-agent --provider anthropic --prompt "your query"

# Container (reads LLM_PROVIDER=anthropic and ANTHROPIC_API_KEY from sre_agent/.env)
docker run -p 8080:8080 --env-file sre_agent/.env my_custom_sre_agent:latest

# Deployment (reads from deployment/.env, can override via environment variable)
LLM_PROVIDER=anthropic ./deployment/build_and_deploy.sh my_custom_sre_agent

# Override API key via environment variable (if not in deployment/.env)
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-xxx ./deployment/build_and_deploy.sh my_custom_sre_agent
```

## Troubleshooting

### Common Issues

1. **Gateway Token Issues**
   ```bash
   # Verify token is set
   echo $GATEWAY_ACCESS_TOKEN
   # Or check .env file
   cat sre_agent/.env
   ```

2. **Provider Configuration**
   ```bash
   # For Anthropic, ensure API key is valid
   echo $ANTHROPIC_API_KEY
   # Test API key with a simple call
   ```

3. **Debug Information**
   ```bash
   # Enable debug mode to see detailed logs
   DEBUG=true uv run sre-agent --prompt "test"
   ```

4. **Container Issues**
   ```bash
   # Check container logs
   docker logs <container_id>
   # Run with debug
   docker run -e DEBUG=true ... my_custom_sre_agent:latest
   ```

### Verification Steps

1. **CLI Working**: Agent responds to queries locally
2. **Container Working**: Container responds to curl requests
3. **AgentCore Working**: Deployed agent responds via invoke script

## Quick Start: Copy-Paste Command Sequence

For a complete deployment using `my_custom_sre_agent`, copy and paste these commands in sequence:

### 1. Build Local Container
```bash
LOCAL_BUILD=true ./deployment/build_and_deploy.sh my_custom_sre_agent
```

### 2. Test Local Container (Bedrock)
```bash
docker run -p 8080:8080 --env-file sre_agent/.env my_custom_sre_agent:latest
```

### 3. Test with curl
```bash
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "prompt": "list the pods in my infrastructure"
    }
  }'
```

### 4. Deploy to AgentCore
```bash
./deployment/build_and_deploy.sh my_custom_sre_agent
```

### 5. Test AgentCore Deployment
```bash
uv run python deployment/invoke_agent_runtime.py \
  --prompt "list the pods in my infrastructure"
```

## Best Practices

1. **Development**: Always test locally first
2. **Environment Files**: Use `.env` files for consistent configuration
3. **Debug Mode**: Enable debug mode when troubleshooting
4. **Provider Testing**: Test both Bedrock and Anthropic providers if using both
5. **Incremental Deployment**: Deploy to staging environment before production

