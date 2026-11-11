# Hosting MCP Server on AgentCore Runtime - CDK

## Overview

This CDK stack deploys an MCP (Model Context Protocol) server on Amazon Bedrock AgentCore Runtime. It demonstrates how to host MCP tools on AgentCore Runtime using infrastructure as code, with automated deployment scripts for a streamlined experience.

The stack uses the Amazon Bedrock AgentCore Python SDK to wrap agent functions as an MCP server compatible with Amazon Bedrock AgentCore. It handles the MCP server details so you can focus on your agent's core functionality.

When hosting tools, the Amazon Bedrock AgentCore Python SDK implements the [Stateless Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) transport protocol with the `MCP-Session-Id` header for session isolation. Your MCP server will be hosted on port `8000` and provide one invocation path: the `mcp-POST` endpoint.

### Tutorial Details

| Information         | Details                                                   |
|:--------------------|:----------------------------------------------------------|
| Tutorial type       | Hosting Tools                                             |
| Tool type           | MCP server                                                |
| Tutorial components | CDK, AgentCore Runtime, MCP server                       |
| Tutorial vertical   | Cross-vertical                                            |
| Example complexity  | Easy                                                      |
| SDK used            | Amazon BedrockAgentCore Python SDK and MCP Client         |

### Architecture

![MCP Server AgentCore Runtime Architecture](architecture.png)

This CDK stack deploys a simple MCP server with 3 tools: `add_numbers`, `multiply_numbers`, and `greet_user`.

The architecture consists of:

- **User/MCP Client**: Sends requests to the MCP server with JWT authentication
- **Amazon Cognito**: Provides JWT-based authentication
  - User Pool with pre-created test user (testuser/MyPassword123!)
  - User Pool Client for application access
- **AWS CodeBuild**: Builds the ARM64 Docker container image with the MCP server
- **Amazon ECR Repository**: Stores the container image
- **AgentCore Runtime**: Hosts the MCP Server
  - **MCP Server**: Exposes three tools via HTTP transport
    - `add_numbers`: Adds two numbers
    - `multiply_numbers`: Multiplies two numbers
    - `greet_user`: Greets a user by name
  - Validates JWT tokens from Cognito
  - Processes MCP tool invocations
- **IAM Roles**: 
  - IAM role for CodeBuild (builds and pushes images)
  - IAM role for AgentCore Runtime (runtime permissions)

### Key Features

* **Complete Infrastructure as Code** - Full CDK implementation
* **Secure by Default** - JWT authentication with Cognito
* **Automated Build** - CodeBuild creates ARM64 Docker images
* **Easy Testing** - Automated test script included
* **Simple Cleanup** - One command removes all resources

## What Gets Deployed

The CDK stack creates:

- **Amazon ECR Repository** - Stores the MCP server Docker image
- **AWS CodeBuild Project** - Builds ARM64 Docker image automatically  
- **Amazon Cognito User Pool** - JWT authentication
- **Cognito User Pool Client** - Application client configuration
- **Cognito User** - Pre-created test user (testuser/MyPassword123!)
- **IAM Roles** - Least-privilege permissions for all services
- **Lambda Functions** - Custom resource automation
- **Amazon Bedrock AgentCore Runtime** - Hosts the MCP server

**MCP Server Tools**:
- `add_numbers` - Adds two numbers together
- `multiply_numbers` - Multiplies two numbers  
- `greet_user` - Greets a user by name

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Deployment](#deployment)
- [Testing](#testing)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)

## Overview

This CDK stack creates an MCP server with JWT authentication using Amazon Cognito. The MCP server provides three tools: `add_numbers`, `multiply_numbers`, and `greet_user`.

### Key Features

* **Complete Infrastructure as Code** - Full CDK implementation
* **Secure by Default** - JWT authentication with Cognito
* **Automated Build** - CodeBuild creates ARM64 Docker images
* **Easy Testing** - Automated test script included
* **Simple Cleanup** - One command removes all resources

## Architecture

The architecture consists of:

- **User/MCP Client**: Sends requests to the MCP server with JWT authentication
- **Amazon Cognito**: Provides JWT-based authentication
  - User Pool with pre-created test user (testuser/MyPassword123!)
  - User Pool Client for application access
- **AWS CodeBuild**: Builds the ARM64 Docker container image with the MCP server
- **Amazon ECR Repository**: Stores the container image
- **AgentCore Runtime**: Hosts the MCP Server
  - **MCP Server**: Exposes three tools via HTTP transport
    - `add_numbers`: Adds two numbers
    - `multiply_numbers`: Multiplies two numbers
    - `greet_user`: Greets a user by name
  - Validates JWT tokens from Cognito
  - Processes MCP tool invocations
- **IAM Roles**: 
  - IAM role for CodeBuild (builds and pushes images)
  - IAM role for AgentCore Runtime (runtime permissions)

## What Gets Deployed

The CDK stack creates:

- **Amazon ECR Repository** - Stores the MCP server Docker image
- **AWS CodeBuild Project** - Builds ARM64 Docker image automatically  
- **Amazon Cognito User Pool** - JWT authentication
- **Cognito User Pool Client** - Application client configuration
- **Cognito User** - Pre-created test user (testuser/MyPassword123!)
- **IAM Roles** - Least-privilege permissions for all services
- **Lambda Functions** - Custom resource automation
- **Amazon Bedrock AgentCore Runtime** - Hosts the MCP server

**MCP Server Tools**:
- `add_numbers` - Adds two numbers together
- `multiply_numbers` - Multiplies two numbers  
- `greet_user` - Greets a user by name

## Prerequisites

- AWS CLI configured with appropriate credentials
- AWS account with permissions to create:
  - CloudFormation stacks
  - ECR repositories
  - CodeBuild projects
  - Cognito User Pools
  - IAM roles and policies
  - Lambda functions
  - Bedrock AgentCore Runtime
- Python 3.10+ and AWS CDK v2 installed
- CDK version 2.220.0 or later (for BedrockAgentCore support)

## Deployment

### CDK vs CloudFormation

This is the **CDK version** of the MCP server AgentCore runtime. If you prefer CloudFormation, see the [CloudFormation version](../../cloudformation/mcp-server-agentcore-runtime/).

### Option 1: Quick Deploy (Recommended)

```bash
# Install dependencies
pip install -r requirements.txt

# Bootstrap CDK (first time only)
cdk bootstrap

# Deploy
cdk deploy
```

### Option 2: Step by Step

```bash
# 1. Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Bootstrap CDK in your account/region (first time only)
cdk bootstrap

# 4. Synthesize the CloudFormation template (optional)
cdk synth

# 5. Deploy the stack
cdk deploy --require-approval never

# 6. Get outputs
cdk list
```

### Deployment Time

The deployment takes approximately **10-15 minutes** and includes:
- Creating all AWS resources
- Building the Docker image
- Pushing to ECR
- Starting the AgentCore Runtime

## Testing

### 1. Get Authentication Token

First, get a JWT token from Cognito:

```bash
# Get the Cognito User Pool Client ID from CDK outputs
CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name MCPServerDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolClientId`].OutputValue' \
  --output text)

# Get authentication token
python get_token.py $CLIENT_ID testuser MyPassword123! us-east-1
```

This will output a JWT token. Copy the token for the next step.

### 2. Test the MCP Server

```bash
# Get the MCP Server Runtime ARN
RUNTIME_ARN=$(aws cloudformation describe-stacks \
  --stack-name MCPServerDemo \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPServerRuntimeArn`].OutputValue' \
  --output text)

# Test the MCP server (replace YOUR_JWT_TOKEN with the token from step 1)
python test_mcp_server.py $RUNTIME_ARN YOUR_JWT_TOKEN us-east-1
```

This will:
- Connect to the MCP server
- List available tools
- Test all three MCP tools
- Display the results

### Expected Output

```
🔄 Initializing MCP session...
✓ MCP session initialized

🔄 Listing available tools...

📋 Available MCP Tools:
==================================================
🔧 add_numbers: Add two numbers together
🔧 multiply_numbers: Multiply two numbers together
🔧 greet_user: Greet a user by name

🧪 Testing MCP Tools:
==================================================

➕ Testing add_numbers(5, 3)...
   Result: 8

✖️  Testing multiply_numbers(4, 7)...
   Result: 28

👋 Testing greet_user('Alice')...
   Result: Hello, Alice! Nice to meet you.

✅ MCP tool testing completed!
```

## Cleanup

### Using CDK (Recommended)

```bash
cdk destroy
```

### Using AWS CLI

```bash
aws cloudformation delete-stack \
  --stack-name MCPServerDemo \
  --region us-east-1

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name MCPServerDemo \
  --region us-east-1
```

## Troubleshooting

### CDK Bootstrap Required

If you see bootstrap errors:
```bash
cdk bootstrap aws://ACCOUNT-NUMBER/REGION
```

### Permission Issues

Ensure your IAM user/role has:
- `CDKToolkit` permissions or equivalent
- Permissions to create all resources in the stack
- `iam:PassRole` for service roles

### Python Dependencies

Install dependencies in the project directory:
```bash
pip install -r requirements.txt
```

For testing, you may need additional packages:
```bash
pip install boto3 "mcp<2.0.0"
```

### Authentication Issues

If authentication fails:
- Verify the Cognito User Pool Client ID is correct
- Ensure you're using the correct region
- Check that the user exists and password is correct
- Verify USER_PASSWORD_AUTH is enabled for the client

### Build Failures

Check CodeBuild logs in the AWS Console:
1. Go to CodeBuild console
2. Find the build project (name contains "mcp-server-build")
3. Check build history and logs

## Cost Estimate

### Monthly Cost Breakdown (us-east-1)

| Service | Usage | Monthly Cost |
|---------|-------|--------------|
| **AgentCore Runtime** | 1 runtime, minimal usage | ~$5-10 |
| **ECR Repository** | 1 repository, <1GB storage | ~$0.10 |
| **CodeBuild** | Occasional builds | ~$1-2 |
| **Lambda** | Custom resource executions | ~$0.01 |
| **Cognito User Pool** | 1 user pool, minimal usage | ~$0.01 |
| **CloudWatch Logs** | Agent logs | ~$0.50 |

**Estimated Total: ~$7-13/month**

### Cost Optimization Tips

- **Delete when not in use**: Use `cdk destroy` to remove all resources
- **Monitor usage**: Set up CloudWatch billing alarms
- **Optimize builds**: Only rebuild when code changes

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](../../CONTRIBUTING.md) for details.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](../../LICENSE) file for details.
