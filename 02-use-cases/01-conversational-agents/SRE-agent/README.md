# SRE Agent - Multi-Agent Site Reliability Engineering Assistant

## Overview

The SRE Agent is a multi-agent system for Site Reliability Engineers that helps investigate infrastructure issues. Built on the Model Context Protocol (MCP) and powered by Amazon Nova and Anthropic Claude models (Claude can be accessed through Amazon Bedrock or directly through Anthropic), this system uses specialized AI agents that collaborate to investigate issues, analyze logs, monitor performance metrics, and execute operational procedures. The AgentCore Gateway provides access to data sources and systems available as MCP tools. This example also demonstrates how to deploy the agent using the Amazon Bedrock AgentCore Runtime for production environments.

### Use case details
| Information         | Details                                                                                                                             |
|---------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| Use case type       | conversational                                                                                                                      |
| Agent type          | Multi-agent                                                                                                                         |
| Use case components | Tools (MCP-based), observability (logs, metrics), operational runbooks                                                             |
| Use case vertical   | DevOps/SRE                                                                                                                          |
| Example complexity  | Advanced                                                                                                                            |
| SDK used            | Amazon Bedrock AgentCore SDK, LangGraph, MCP                                                                                       |

## Assets

| Asset | Description |
|-------|-------------|
| [Demo video 1 (SRE-Agent CLI, VSCode integration)](https://github.com/user-attachments/assets/c28087a6-7a97-43f0-933d-28e3f6e2eeeb) | Walkthrough of the SRE Agent investigating and resolving infrastructure issues using CLI and VSCode |
| [Demo video 2 (Cursor integration)](https://github.com/user-attachments/assets/c1a3c26b-e982-4842-bed0-8e668d79269e) | Demonstration of AgentCore Gateway with SRE tools integration with Cursor IDE |
| [AI generated podcast](https://github.com/user-attachments/assets/feedb9d2-064c-4c5e-a306-94941065cf82) | Audio discussion explaining the SRE Agent's capabilities and architecture |

### Use case Architecture 

![SRE support agent with Amazon Bedrock AgentCore](docs/images/sre-agent-architecture.png)

### Use case key Features

- **Multi-Agent Orchestration**: Specialized agents collaborate on infrastructure investigations with real-time streaming
- **Conversational Interface**: Single-query investigations and interactive multi-turn conversations with context preservation
- **Long-term Memory Integration**: Amazon Bedrock Agent Memory provides persistent user preferences and infrastructure knowledge across sessions
- **User Personalization**: Tailored reports and escalation procedures based on individual user preferences and roles
- **MCP-based Integration**: AgentCore Gateway provides secure API access with authentication and health monitoring
- **Specialized Agents**: Four domain-specific agents for Kubernetes, logs, metrics, and operational procedures
- **Documentation and Reporting**: Markdown reports generated for each investigation with audit trail

## Detailed Documentation

For comprehensive information about the SRE Agent system, please refer to the following detailed documentation:

- **[System Components](docs/system-components.md)** - In-depth architecture and component explanations
- **[Memory System](docs/memory-system.md)** - Long-term memory integration, user personalization, and cross-session learning
- **[Configuration](docs/configuration.md)** - Complete configuration guides for environment variables, agents, and gateway
- **[Deployment Guide](docs/deployment-guide.md)** - Complete deployment guide for Amazon Bedrock AgentCore Runtime
- **[Security](docs/security.md)** - Security best practices and considerations for production deployment
- **[Demo Environment](docs/demo-environment.md)** - Demo scenarios, data customization, and testing setup
- **[Example Use Cases](docs/example-use-cases.md)** - Detailed walkthroughs and interactive troubleshooting examples
- **[Verification](docs/verification.md)** - Ground truth verification and report validation
- **[Development](docs/development.md)** - Testing, code quality, and contribution guidelines


## Prerequisites

| Requirement | Description |
|-------------|-------------|
| Python 3.12+ and `uv` | Python runtime and package manager. See [use-case setup](#use-case-setup) |
| Amazon EC2 Instance | Recommended: `t3.xlarge` or larger |
| Valid SSL certificates | **⚠️ IMPORTANT:** Amazon Bedrock AgentCore Gateway **only works with HTTPS endpoints**. For example, you can register your Amazon EC2 with [no-ip.com](https://www.noip.com/) and obtain a certificate from [letsencrypt.org](https://letsencrypt.org/), or use any other domain registration and SSL certificate provider. You'll need the domain name as `BACKEND_DOMAIN` and certificate paths in the [use-case setup](#use-case-setup) section |
| EC2 instance port configuration | Required inbound ports (443, 8011-8014). See [EC2 instance port configuration](docs/ec2-port-configuration.md) |
| IAM role with BedrockAgentCoreFullAccess policy | Required permissions and trust policy for AgentCore service. See [IAM role with BedrockAgentCoreFullAccess policy](docs/auth.md) |
| Identity Provider (IDP) | Amazon Cognito, Auth0, or Okta for JWT authentication. For automated Cognito setup, use `deployment/setup_cognito.sh`. See [Authentication setup](docs/auth.md#identity-provider-configuration) |

> **Note:** All prerequisites must be completed before proceeding to the use case setup. The setup will fail without proper SSL certificates, IAM permissions, and identity provider configuration.

## Use case setup

> **Configuration Guide**: For detailed information about all configuration files used in this project, see the [Configuration Documentation](docs/configuration.md).

```bash
# Clone the repository
git clone https://github.com/awslabs/amazon-bedrock-agentcore-samples
cd amazon-bedrock-agentcore-samples/02-use-cases/SRE-agent

# Create and activate a virtual environment
uv venv --python 3.12
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install the SRE Agent and dependencies
uv pip install -e .

# Configure environment variables
cp sre_agent/.env.example sre_agent/.env
# Edit sre_agent/.env and add your Anthropic API key if using Anthropic directly:
# ANTHROPIC_API_KEY=sk-ant-your-key-here
# 
# Note: If you are using Amazon Bedrock models for inference, you do not need to
# make any changes to the .env file - the defaults are good to get started

# Openapi Templates get replaced with your backend domain and saved as .yaml
BACKEND_DOMAIN=api.mycompany.com ./backend/openapi_specs/generate_specs.sh

# Set backend startup control variable (set to 0, false, or no to skip backend startup)
# This is useful when backend API servers are already running elsewhere,
# such as in workshop studio accounts or shared environments
START_API_BACKEND=${START_API_BACKEND:-1}

if [[ "$START_API_BACKEND" =~ ^(1|true|yes)$ ]]; then
  echo "Starting backend API servers..."
  
  # Get your EC2 instance private IP for server binding
  TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" -s)
  PRIVATE_IP=$(curl -H "X-aws-ec2-metadata-token: $TOKEN" \
    -s http://169.254.169.254/latest/meta-data/local-ipv4)

  # Start the demo backend servers with SSL
  cd backend
  ./scripts/start_demo_backend.sh \
    --host $PRIVATE_IP  \
    --ssl-keyfile /opt/ssl/privkey.pem \
    --ssl-certfile /opt/ssl/fullchain.pem
  cd ..
else
  echo "Skipping backend API server startup (START_API_BACKEND is set to $START_API_BACKEND)"
fi

# Configure the AgentCore Gateway
cd gateway
cp config.yaml.example config.yaml
# Edit config.yaml and update the parameter values
# The file contains helpful comments for each parameter
# Key parameters to update:
#   - account_id: Your AWS account ID
#   - region: Your AWS region
#   - role_name: IAM role with BedrockAgentCoreFullAccess
#     For testing: Can be your current EC2/notebook role (run: aws sts get-caller-identity)
#     For production: Use a dedicated gateway role (recommended)
#   - user_pool_id and client_id: From Cognito setup
#   - s3_bucket: Your S3 bucket for OpenAPI schemas
# See gateway/config.yaml for detailed parameter descriptions

# Create and configure the AgentCore Gateway
./create_gateway.sh
./mcp_cmds.sh
cd ..

# Update the gateway URI in agent configuration
GATEWAY_URI=$(cat gateway/.gateway_uri)
sed -i "s|uri: \".*\"|uri: \"$GATEWAY_URI\"|" sre_agent/config/agent_config.yaml

# Copy the gateway access token to your .env file
sed -i '/^GATEWAY_ACCESS_TOKEN=/d' sre_agent/.env
echo "GATEWAY_ACCESS_TOKEN=$(cat gateway/.access_token)" >> sre_agent/.env

# Initialize memory system and add user preferences
uv run python scripts/manage_memories.py update

# Note: Memory system takes 10-12 minutes to be ready
# Check memory status after 10 minutes:
uv run python scripts/manage_memories.py list

# Once memory shows as ready, run update again to ensure preferences are loaded:
uv run python scripts/manage_memories.py update
```

> **Local Setup Complete**: Your SRE Agent is now running locally on your EC2 instance and is exercising the AgentCore Gateway and Memory services. If you want to deploy this agent on AgentCore Runtime so you can integrate it into your applications (like a chatbot, Slack bot, etc.), follow the instructions in the [Development to Production Deployment Flow](#development-to-production-deployment-flow) section below.

## Execution instructions

### Memory-Enhanced Personalized Investigations

The SRE Agent includes a sophisticated memory system that personalizes investigations based on user preferences. The system comes preconfigured with two user personas in [`scripts/user_config.yaml`](scripts/user_config.yaml):

- **Alice**: Technical detailed investigations with comprehensive analysis and team alerts
- **Carol**: Executive-focused investigations with business impact analysis and strategic alerts

When running investigations with different user IDs, the agent produces similar technical findings but presents them according to each user's preferences:

```bash
# Alice's detailed technical investigation
USER_ID=Alice sre-agent --prompt "API response times have degraded 3x in the last hour" --provider bedrock

# Carol's executive-focused investigation  
USER_ID=Carol sre-agent --prompt "API response times have degraded 3x in the last hour" --provider bedrock
```

Both commands will identify identical technical issues but present them differently:
- **Alice** receives detailed technical analysis with step-by-step troubleshooting and team notifications
- **Carol** receives executive summaries focused on business impact with rapid escalation timelines

For a detailed comparison showing how the memory system personalizes identical incidents, see: [**Memory System Report Comparison**](docs/examples/Memory_System_Analysis_User_Personalization_20250802_162648.md)

### Single Query Mode
```bash
# Investigate specific pod issues
sre-agent --prompt "Why are the payment-service pods crash looping?"

# Analyze performance degradation
sre-agent --prompt "Investigate high latency in the API gateway over the last hour"

# Search for error patterns
sre-agent --prompt "Find all database connection errors in the last 24 hours"
```

### Interactive Mode
```bash
# Start interactive conversation
sre-agent --interactive

# Available commands in interactive mode:
# /help     - Show available commands
# /agents   - List available specialist agents
# /history  - Show conversation history
# /save     - Save the current conversation
# /clear    - Clear conversation history
# /exit     - Exit the interactive session
```

#### Advanced Options
```bash
# Use Amazon Bedrock
sre-agent --provider bedrock --query "Check cluster health"

# Save investigation reports to custom directory
sre-agent --output-dir ./investigations --query "Analyze memory usage trends"

# Use Amazon Bedrock with specific profile
AWS_PROFILE=production sre-agent --provider bedrock --interactive
```

## Development to Production Deployment Flow

The SRE Agent follows a structured deployment process from local development to production on Amazon Bedrock AgentCore Runtime. For detailed instructions, see the **[Deployment Guide](docs/deployment-guide.md)**.

```
STEP 1: LOCAL DEVELOPMENT
┌─────────────────────────────────────────────────────────────────────┐
│  Develop Python Package (sre_agent/)                                │
│  └─> Test locally with CLI: uv run sre-agent --prompt "..."         │
│      └─> Agent connects to AgentCore Gateway via MCP protocol       │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 2: CONTAINERIZATION  
┌─────────────────────────────────────────────────────────────────────┐
│  Add agent_runtime.py (FastAPI server wrapper)                      │
│  └─> Create Dockerfile (ARM64 for AgentCore)                        │
│      └─> Uses deployment/build_and_deploy.sh script                 │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 3: LOCAL CONTAINER TESTING
┌─────────────────────────────────────────────────────────────────────┐
│  Build: LOCAL_BUILD=true ./deployment/build_and_deploy.sh           │
│  └─> Run: docker run -p 8080:8080 sre_agent:latest                  │
│      └─> Test: curl -X POST http://localhost:8080/invocations       │
│          └─> Container connects to same AgentCore Gateway           │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
STEP 4: PRODUCTION DEPLOYMENT
┌─────────────────────────────────────────────────────────────────────┐
│  Build & Push: ./deployment/build_and_deploy.sh                     │
│  └─> Pushes container to Amazon ECR                                 │
│      └─> deployment/deploy_agent_runtime.py deploys to AgentCore    │
│          └─> Test: uv run python deployment/invoke_agent_runtime.py │
│              └─> Production agent uses production Gateway           │
└─────────────────────────────────────────────────────────────────────┘

Key Points:
• Core agent code (sre_agent/) remains unchanged
• Deployment/ folder contains all deployment-specific utilities
• Same agent works locally and in production via environment config
• AgentCore Gateway provides MCP tools access at all stages
```

## Deploying Your Agent on Amazon Bedrock AgentCore Runtime

For production deployments, you can deploy the SRE Agent directly to Amazon Bedrock AgentCore Runtime. This provides a scalable, managed environment for running your agent with enterprise-grade security and monitoring.

The AgentCore Runtime deployment supports:
- **Container-based deployment** with automatic scaling
- **Multiple LLM providers** (Amazon Bedrock or Anthropic Claude)
- **Debug mode** for troubleshooting and development
- **Environment-based configuration** for different deployment stages
- **Secure credential management** through AWS IAM and environment variables

For complete step-by-step instructions including local testing, container building, and production deployment, see the **[Deployment Guide](docs/deployment-guide.md)**.

## AgentCore Observability

Adding observability to an Agent deployed on the AgentCore Runtime is straightforward using the observability primitive. This enables comprehensive monitoring through Amazon CloudWatch with metrics, traces, and logs.

### Setting Up Observability

#### 1. Add OpenTelemetry Packages

The required OpenTelemetry packages are already included in `pyproject.toml`:

```toml
dependencies = [
    # ... other dependencies ...
    "opentelemetry-instrumentation-langchain",
    "aws-opentelemetry-distro~=0.10.1",
]
```

#### 2. Configure Observability for Agents

Follow the [Amazon Bedrock AgentCore observability configuration guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html#observability-configure-builtin) to enable metrics in Amazon CloudWatch.

#### 3. Enable OpenTelemetry Instrumentation

When starting the container, use the `opentelemetry-instrument` utility to automatically instrument your application. This is configured in the Dockerfile:

```dockerfile
# Run application with OpenTelemetry instrumentation
CMD ["uv", "run", "opentelemetry-instrument", "uvicorn", "sre_agent.agent_runtime:app", "--host", "0.0.0.0", "--port", "8080"]
```

### Viewing Metrics and Traces

Once deployed with observability enabled, you can monitor your agent's performance through:

- **Amazon CloudWatch Metrics**: View request rates, latencies, and error rates
- **AWS X-Ray Traces**: Analyze distributed traces to understand request flow
- **CloudWatch Logs**: Access structured logs for debugging and analysis

![Agent Metrics Dashboard](docs/images/agent-metrics.gif)

The observability primitive automatically captures:
- LLM invocation metrics (tokens, latency, model usage)
- Tool execution traces (duration, success/failure)
- Memory operations (retrieval, storage)
- End-to-end request tracing across all agent components

## Maintenance and Operations

### Restarting Backend Servers and Refreshing Access Token

To maintain connectivity with the Amazon Bedrock AgentCore Gateway, you need to periodically restart backend servers and refresh the access token. Run the gateway configuration script:

```bash
# Important: Run this from within the virtual environment
source .venv/bin/activate  # If not already activated
./scripts/configure_gateway.sh
```

**What this script does:**
- **Stops running backend servers** to ensure clean restart
- **Generates a new access token** for AgentCore Gateway authentication
- **Gets the EC2 instance private IP** for proper SSL binding
- **Starts backend servers** with SSL certificates (HTTPS) or HTTP fallback
- **Updates gateway URI** in the agent configuration from `gateway/.gateway_uri`
- **Updates access token** in the `.env` file for agent authentication

**Important:** You must run this script **every 24 hours** because the access token expires after 24 hours. If you don't refresh the token:
- The SRE agent will lose connection to the AgentCore gateway
- No MCP tools will be available (Kubernetes, logs, metrics, runbooks APIs)
- Investigations will fail as agents cannot access backend services

For more details, see the [configure_gateway.sh](scripts/configure_gateway.sh) script.

### Troubleshooting Gateway Connection Issues

If you encounter "gateway connection failed" or "MCP tools unavailable" errors:
1. Check if the access token has expired (24-hour limit)
2. Run `./scripts/configure_gateway.sh` to refresh authentication (from within the virtual environment)
3. Verify backend servers are running with `ps aux | grep python`
4. Check SSL certificate validity if using HTTPS

## Clean up instructions

### Complete AWS Resource Cleanup

For complete cleanup of all AWS resources (Gateway, Runtime, and local files):

```bash
# Complete cleanup - deletes AWS resources and local files
./scripts/cleanup.sh

# Or with custom names
./scripts/cleanup.sh --gateway-name my-gateway --runtime-name my-runtime

# Force cleanup without confirmation prompts
./scripts/cleanup.sh --force
```

This script will:
- Stop backend servers
- Delete the AgentCore Gateway and all its targets
- Delete memory resources
- Delete the AgentCore Runtime
- Remove generated files (gateway URIs, tokens, agent ARNs, memory IDs)

### Manual Local Cleanup Only

If you only want to clean up local files without touching AWS resources:

```bash
# Stop all demo servers
cd backend
./scripts/stop_demo_backend.sh
cd ..

# Clean up generated files only
rm -rf gateway/.gateway_uri gateway/.access_token
rm -rf deployment/.agent_arn .memory_id

# Note: .env, .venv, and reports/ are preserved for development continuity
```

## Disclaimer
The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments. Make sure to have Amazon Bedrock Guardrails in place to protect against [prompt injection](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-injection.html).

**Important Note**: The data in [`backend/data`](backend/data) is synthetically generated, and the backend directory contains stub servers that showcase how a real SRE agent backend could work. In a production environment, these implementations would need to be replaced with real implementations that connect to actual systems, use vector databases, and integrate with other data sources.