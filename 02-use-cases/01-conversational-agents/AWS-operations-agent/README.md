# AWS Support Agent with Amazon Bedrock AgentCore

An AWS support conversational AI system built on Amazon Bedrock AgentCore, featuring OAuth2 authentication, MCP (Model Control Protocol) integration, and comprehensive AWS service operations.

## Demo

![AWS Support Agent Demo](images/demo-agentcore.gif)

*Interactive demonstration of the AWS Support Agent powered by Amazon Bedrock AgentCore*

## Architecture Overview

### High-Level Architecture

![AWS Support Agent High-Level Architecture](images/architecture-2.jpg)

*High-level system architecture showing the complete AgentCore ecosystem with observability integration*

### Detailed Authentication Flow

![AWS Support Agent Authentication Flow](images/flow.jpg)

*Detailed sequence diagram showing OAuth2 authentication flow and token management across AgentCore components*

![AWS Support Agent Architecture](images/architecture.jpg)

*Core system architecture showing component interactions and data flow*

The system follows a secure, distributed architecture:

1. **Chat Client** authenticates users via Okta OAuth2 and sends questions with JWT tokens
2. **AgentCore Runtime** validates tokens, processes conversations, and maintains session memory
3. **AgentCore Gateway** provides secure tool access through MCP protocol
4. **AWS Lambda Target** executes AWS service operations with proper authentication
5. **AgentCore Identity** manages workload authentication and token exchange
6. **AgentCore Observability** provides comprehensive monitoring, metrics, and logging capabilities

## Key Features

- 🔐 **Enterprise Authentication**: Okta OAuth2 with JWT token validation
- 🤖 **Dual Agent Architecture**: Both FastAPI (DIY) and BedrockAgentCoreApp (SDK) implementations
- 🧠 **Conversation Memory**: Persistent session storage with AgentCore Memory
- 🔗 **MCP Integration**: Standardized tool communication protocol
- 🛠️ **20+ AWS Tools**: Comprehensive read-only operations across AWS services
- 📊 **Production Ready**: Complete deployment automation and infrastructure management

## Project Structure

```
AgentCore/
├── README.md                           # This documentation
├── requirements.txt                    # Python dependencies
├── config/                             # 🔧 Configuration management
│   ├── static-config.yaml              # Manual configuration settings
│   └── dynamic-config.yaml             # Runtime-generated configuration
│
├── shared/                             # 🔗 Shared configuration utilities
│   ├── config_manager.py               # Central configuration management
│   └── config_validator.py             # Configuration validation
│
├── chatbot-client/                     # 🤖 Client application
│   ├── src/client.py                   # Interactive chat client
│   └── README.md                       # Client-specific documentation
│
├── agentcore-runtime/                  # 🚀 Main runtime implementation
│   ├── src/
│   │   ├── agents/                     # Agent implementations
│   │   │   ├── diy_agent.py            # FastAPI implementation
│   │   │   └── sdk_agent.py            # BedrockAgentCoreApp implementation
│   │   ├── agent_shared/               # Shared agent utilities
│   │   │   ├── auth.py                 # JWT validation
│   │   │   ├── config.py               # Agent configuration
│   │   │   ├── mcp.py                  # MCP client
│   │   │   ├── memory.py               # Conversation memory
│   │   │   └── responses.py            # Response formatting
│   │   └── utils/
│   │       └── memory_manager.py       # Memory management utilities
│   ├── deployment/                     # 🚀 Deployment scripts
│   │   ├── 01-prerequisites.sh         # IAM roles and prerequisites
│   │   ├── 02-create-memory.sh         # AgentCore Memory setup
│   │   ├── 03-setup-oauth-provider.sh  # OAuth2 provider configuration
│   │   ├── 04-deploy-mcp-tool-lambda.sh # MCP Lambda deployment
│   │   ├── 05-create-gateway-targets.sh # Gateway and targets setup
│   │   ├── 06-deploy-diy.sh            # DIY agent deployment
│   │   ├── 07-deploy-sdk.sh            # SDK agent deployment
│   │   ├── 08-delete-runtimes.sh       # Runtime cleanup
│   │   ├── 09-delete-gateways-targets.sh # Gateway cleanup
│   │   ├── 10-delete-mcp-tool-deployment.sh # MCP cleanup
│   │   ├── 11-delete-oauth-provider.sh # OAuth provider cleanup
│   │   ├── 12-delete-memory.sh         # Memory cleanup
│   │   ├── 13-cleanup-everything.sh    # Complete cleanup script
│   │   ├── bac-permissions-policy.json # IAM permissions policy
│   │   ├── bac-trust-policy.json       # IAM trust policy
│   │   ├── Dockerfile.diy              # DIY agent container
│   │   ├── Dockerfile.sdk              # SDK agent container
│   │   ├── deploy-diy-runtime.py       # DIY deployment automation
│   │   └── deploy-sdk-runtime.py       # SDK deployment automation
│   ├── gateway-ops-scripts/            # 🌉 Gateway management
│   │   └── [Gateway CRUD operations]
│   ├── runtime-ops-scripts/            # ⚙️ Runtime management
│   │   └── [Runtime and identity management]
│   └── tests/local/                    # 🧪 Local testing scripts
│
├── mcp-tool-lambda/                    # 🔧 AWS Tools Lambda
│   ├── lambda/mcp-tool-handler.py      # MCP tool implementation
│   ├── mcp-tool-template.yaml          # CloudFormation template
│   └── deploy-mcp-tool.sh              # Lambda deployment script
│
├── okta-auth/                          # 🔐 Authentication setup
│   ├── OKTA-OPENID-PKCE-SETUP.md      # Okta configuration guide
│   ├── iframe-oauth-flow.html          # OAuth flow testing
│   └── setup-local-nginx.sh           # Local development setup
│
└── docs/                               # 📚 Documentation
    └── images/
        └── agentcore-implementation.jpg # Architecture diagram
```

## Quick Start

### Prerequisites

- **AWS CLI** configured with appropriate permissions
- **Docker** and Docker Compose installed
- **Python 3.11+** installed
- **Okta developer account** and application configured
- **yq** tool for YAML processing (optional, fallback available)

### 1. Configure Settings

Edit the configuration files with your specific settings:

```bash
# Configure AWS and Okta settings - Ensure you update the static config 
#Please ensure you update below files by replacing the place holders <your-aws-account-id>, <YOUR_OKTA_DOMAIN>, <YOUR_OKTA_CLIENT_ID> and #<YOUR_OKTA_AUTHORIZATION_SERVER_AUDIENCE> 
vim config/static-config.yaml

# Key settings to update:
# - aws.account_id: Your AWS account ID
# - aws.region: Your preferred AWS region  (this project tested on us-east-1)
# - okta.domain: Your Okta domain
# - okta.client_credentials.client_id: Your Okta client ID

# IMPORTANT: Update IAM policy files with your AWS account ID
# Replace YOUR_AWS_ACCOUNT_ID placeholder in these files:
sed -i "s/YOUR_AWS_ACCOUNT_ID/$(aws sts get-caller-identity --query Account --output text)/g" \
  agentcore-runtime/deployment/bac-permissions-policy.json \
  agentcore-runtime/deployment/bac-trust-policy.json

# Verify the account ID was updated correctly
grep -n "$(aws sts get-caller-identity --query Account --output text)" \
  agentcore-runtime/deployment/bac-permissions-policy.json \
  agentcore-runtime/deployment/bac-trust-policy.json
```

### 2. Deploy Infrastructure

Run the deployment scripts in sequence:

#### Note: `Before running below scripts please ensure you have successfully setup Okta and can generate access token using http://localhost:8080/okta-auth/iframe-oauth-flow.html - Refer OKTA-OPENID-PKCE-SETUP.md for details. [/AWS-operations-agent/okta-auth/OKTA-OPENID-PKCE-SETUP.md]`

```bash
cd agentcore-runtime/deployment

# Please ensure you update below files replacing the place holders <your-aws-account-id>, <YOUR_OKTA_DOMAIN>, <YOUR_OKTA_CLIENT_ID> and <YOUR_OKTA_AUTHORIZATION_SERVER_AUDIENCE> before running ./01-prerequisites.sh
# bac-permissions-policy.json
# bac-trust-policy.json
# Ensure latest AWS Cli is installed and AWS Cli  credentails file has default profile populated or export AWS credentials on terminal
# Ensure the AWS Account you are using has anthropic models enabled by going to Bedrock > Model Access page in us-east-1 (N. Virginia region)
./01-prerequisites.sh

# If you get error 'aws bedrock-agentcore-control is not available'
# please update your aws cli to latest version using
# curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "AWSCLIV2.pkg"
# sudo installer -pkg AWSCLIV2.pkg -target /

# Create AgentCore Memory for conversation storage
./02-create-memory.sh

# Set up Okta OAuth2 provider - This setup is for outbound auth from AgentCore Runtime to AgentCore Gateway EndPoint.
# Please ensure you OKTA-OPENID-PKCE-SETUP.md file to setup SPA app for client side - inbound auth with runtime
# And also setup new api services app for outbound auth between AgentCore Runtime and AgentCore Gateway EndPoint.
# You need to add the client id/client secret of the Okta App 'App name: aws-support-agent-m2m' when executing below script as,
# that will create a credentails provider where these secrets will be stored. Go to Okta Console > Applications > Applications > aws-support-agent-m2m and copy cliend id and client secret. We are assuming you have create new scope 'api' in Okta per OKTA-OPENID-PKCE-SETUP.md and therefore you will not need to change value of 'api' when running below script
./03-setup-oauth-provider.sh

# Deploy MCP tools Lambda function
./04-deploy-mcp-tool-lambda.sh

# Create AgentCore Gateway and targets
./05-create-gateway-targets.sh

# Deploy the agents (choose one or both)
./06-deploy-diy.sh    # FastAPI implementation
./07-deploy-sdk.sh    # BedrockAgentCoreApp implementation
```

#### Note: `Scripts above dynamically update the dynamic-config.yaml file. Please ensure the yaml file is correctly updated.`

### 3. Test the System

#### Use the Interactive Chat Client

**For deployed agents:**
```bash
cd chatbot-client
python src/client.py
```

#### Note: `Please use http://localhost:8080/okta-auth/iframe-oauth-flow.html to fetch access token as the client will ask for it`

The client will show you available deployed agents:
```
🤖 AgentCore Chatbot Client
==============================

📦 Available AgentCore Runtimes:
========================================
1. DIY Agent
   Name: bac_runtime_diy
   ARN: arn:aws:bedrock-agentcore:us-east-1:xxxxxxx:runtime/bac_runtime_diy-xxxxx
   Status: ✅ Available
2. SDK Agent
   Name: bac_runtime_sdk
   ARN: arn:aws:bedrock-agentcore:us-east-1:xxxxx:runtime/bac_runtime_sdk-xxxx
   Status: ✅ Available

🎯 Select Runtime:
Enter choice (1 for DIY, 2 for SDK): 
```

#### Interactive Chat Client with Local Containers (optional - advance use case)

#### `Please run below script to deploy agents locally. Agents will be connecting to AgentCore services in AWS. `

```bash
# Start a local agent container
cd agentcore-runtime/tests/local
./run-diy-local-container.sh    # For DIY agent
# OR
./run-sdk-local-container.sh    # For SDK agent

# In another terminal, connect with the chat client
cd chatbot-client
python src/client.py --local
```

#### Note: `The client will show you available local agents and can only connect with one Agent at a time, since both agents are deployed to 8080. Ensure you stop nginx otherwise it will conflict with local deployment. Or else, please change nginx port by updating server block configuration and correspondingly change Okta configurations.`

```
🤖 Local Testing Mode
==============================

📦 Local Testing Mode:
========================================
1. DIY Agent
   Name: Local DIY Agent
   URL: http://localhost:8080
   Status: ✅ Available (if Docker container is running)
2. SDK Agent
   Name: Local SDK Agent
   URL: http://localhost:8080
   Status: ✅ Available (if Docker container is running)

🎯 Select Runtime:
Enter choice (1 for DIY, 2 for SDK):
```

**For local testing with containers:**
```bash
cd chatbot-client
python src/client.py --local
```

The `--local` flag enables local testing mode where you can connect to containerized agents running on localhost:8080.


## Component Details

### Agent Implementations

#### DIY Agent (FastAPI)
- **Framework**: FastAPI with Uvicorn
- **Endpoint**: `/invoke` 
- **Features**: Custom implementation with full control over request/response handling
- **Container**: `agentcore-runtime/deployment/Dockerfile.diy`

#### SDK Agent (BedrockAgentCoreApp)
- **Framework**: BedrockAgentCoreApp SDK
- **Features**: Native AgentCore integration with built-in optimizations
- **Container**: `agentcore-runtime/deployment/Dockerfile.sdk`

### Authentication Flow

1. **User Authentication**: Users authenticate via Okta OAuth2 PKCE flow
2. **Token Validation**: AgentCore Runtime validates JWT tokens using Okta's discovery endpoint
3. **Workload Identity**: Runtime exchanges user tokens for workload access tokens
4. **Service Authentication**: Workload tokens authenticate with AgentCore Gateway and tools

### Memory Management

- **Storage**: AgentCore Memory service provides persistent conversation storage
- **Session Management**: Each conversation maintains session context across interactions
- **Retention**: Configurable retention periods for conversation data
- **Privacy**: Memory isolation per user session

### Tool Integration

The system provides 20+ AWS service tools through the MCP Lambda:

- **EC2**: Instance management and monitoring
- **S3**: Bucket operations and policy analysis
- **Lambda**: Function management and monitoring
- **CloudFormation**: Stack operations and resource tracking
- **IAM**: User, role, and policy management
- **RDS**: Database instance monitoring
- **CloudWatch**: Metrics, alarms, and log analysis
- **Cost Explorer**: Cost analysis and optimization
- **And many more...**

## Configuration Management

### Static Configuration (`config/static-config.yaml`)
Contains manually configured settings:
- AWS account and region settings
- Okta OAuth2 configuration
- Agent model settings
- Tool schemas and definitions

### Dynamic Configuration (`config/dynamic-config.yaml`)
Auto-generated during deployment:
- Runtime ARNs and endpoints
- Gateway URLs and identifiers
- OAuth provider configurations
- Memory service details

### Configuration Manager
The `shared/config_manager.py` provides:
- Unified configuration access
- Environment-specific settings
- Validation and error handling
- Backward compatibility


### Container Development

Both agents follow a standardized container structure:

```
/app/
├── shared/                     # Project-wide utilities
├── agent_shared/              # Agent-specific helpers
├── config/                    # Configuration files
│   ├── static-config.yaml
│   └── dynamic-config.yaml
├── [agent].py                 # Agent implementation
└── requirements.txt
```

### Local Container Scripts

The following scripts provide easy local testing with full containerization:

- **`agentcore-runtime/tests/local/run-diy-local-container.sh`** - Runs DIY agent in Docker container on port 8080
- **`agentcore-runtime/tests/local/run-sdk-local-container.sh`** - Runs SDK agent in Docker container on port 8080

These containers include:
- Full MCP tool integration
- Local tool fallbacks when MCP gateway is unavailable
- Complete agent functionality without AWS deployment
- Isolated testing environment

### Adding New Tools

1. **Define tool schema** in `config/static-config.yaml`
2. **Implement tool logic** in `mcp-tool-lambda/lambda/mcp-tool-handler.py`
3. **Update gateway targets** using gateway-ops-scripts
4. **Test integration** with local test scripts

## Monitoring and Operations

### Runtime Management
```bash
cd agentcore-runtime/runtime-ops-scripts

# List all deployed runtimes
python runtime_manager.py list

# Check runtime details
python runtime_manager.py get <runtime_id>

# Test OAuth flow
python oauth_test.py test-config
```

### Gateway Management
```bash
cd agentcore-runtime/gateway-ops-scripts

# List all gateways
python list-gateways.py

# Check gateway targets
python list-targets.py

# Update gateway configuration
python update-gateway.py --gateway-id <id> --name "New Name"
```

### Log Analysis
- **CloudWatch Logs**: Agent runtime logs
- **Request Tracing**: Full request/response logging
- **Error Monitoring**: Centralized error tracking
- **Performance Metrics**: Response time and resource usage

## Cleanup

To remove all deployed resources:

### Note: `Runtime deletion takes time.`

```bash
cd agentcore-runtime/deployment

# Delete runtimes
./08-delete-runtimes.sh

# Delete gateways and targets  
./09-delete-gateways-targets.sh

# Delete MCP Lambda
./10-delete-mcp-tool-deployment.sh

# Delete Identity - Credentials Provider
./11-delete-oauth-provider.sh

# Delete memory
./12-delete-memory.sh

# Complete cleanup (optional)
./13-cleanup-everything.sh
```

## Security Best Practices

- **Token Validation**: All requests validated against Okta JWT
- **Least Privilege**: IAM roles follow principle of least privilege
- **Encryption**: All data encrypted in transit and at rest
- **Network Security**: Private networking with controlled access
- **Audit Logging**: Comprehensive audit trail for all operations

## Troubleshooting

### Common Issues

1. **Token Validation Failures**
   - Check Okta configuration in `static-config.yaml`
   - Verify JWT audience and issuer settings
   - Test with `oauth_test.py`

2. **Memory Access Issues**
   - Verify AgentCore Memory is deployed and available
   - Check memory configuration in `dynamic-config.yaml`
   - Test memory operations with local scripts

3. **Tool Execution Failures**
   - Check MCP Lambda deployment status
   - Verify gateway target configuration
   - Test individual tools with MCP client

4. **Container Startup Issues**
   - Check Docker build logs
   - Verify requirements.txt compatibility
   - Review container health endpoints

### Getting Help

1. **Check deployment logs** in CloudWatch
2. **Run diagnostic scripts** in runtime-ops-scripts
3. **Verify configuration** with config_manager validation
4. **Test components individually** using local test scripts

## License

This project is for educational and experimental purposes. Please ensure compliance with your organization's policies and AWS service terms.