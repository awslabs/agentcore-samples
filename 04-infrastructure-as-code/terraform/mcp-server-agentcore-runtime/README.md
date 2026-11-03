# MCP Server on AgentCore Runtime - Terraform

This pattern demonstrates deploying an MCP (Model Context Protocol) server on Amazon Bedrock AgentCore Runtime using Terraform. It creates an MCP server with JWT authentication and three custom tools.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Testing the MCP Server](#testing-the-mcp-server)
- [Sample Tool Invocations](#sample-tool-invocations)
- [Customization](#customization)
- [File Structure](#file-structure)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)
- [Pricing](#pricing)
- [Next Steps](#next-steps)
- [Resources](#resources)
- [🤝 Contributing](#-contributing)
- [📄 License](#-license)

## Overview

This Terraform configuration creates an MCP server deployment that includes:

- **MCP Server**: Hosts three custom tools (add_numbers, multiply_numbers, greet_user)
- **JWT Authentication**: Cognito User Pool for secure access
- **AgentCore Runtime**: Serverless hosting with MCP protocol support
- **ECR Repository**: Stores the Docker container image
- **CodeBuild Project**: Automatically builds the ARM64 Docker image

The stack uses the Amazon Bedrock AgentCore Python SDK to wrap agent functions as an MCP server compatible with Amazon Bedrock AgentCore. When hosting tools, the SDK implements the [Stateless Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) transport protocol with the `MCP-Session-Id` header for session isolation.

This makes it ideal for:
- Learning MCP protocol with AgentCore Runtime
- Building secure MCP servers with JWT authentication
- Understanding MCP tool development patterns
- Creating custom tools for AI agents

### Tutorial Details

| Information         | Details                                                   |
|:--------------------|:----------------------------------------------------------|
| Tutorial type       | Hosting Tools                                             |
| Tool type           | MCP server                                                |
| Tutorial components | Terraform, AgentCore Runtime, MCP server, Cognito        |
| Tutorial vertical   | Cross-vertical                                            |
| Example complexity  | Intermediate                                              |
| SDK used            | Amazon BedrockAgentCore Python SDK and MCP Client         |

## Architecture

![Architecture Diagram](architecture.png)

The architecture consists of:

- **User/MCP Client**: Sends requests to the MCP server with JWT authentication
- **Amazon Cognito**: Provides JWT-based authentication
  - User Pool with pre-created test user (testuser/MyPassword123!)
  - User Pool Client for application access
- **AWS CodeBuild**: Builds the ARM64 Docker container image with the MCP server
- **Amazon ECR Repository**: Stores the container image
- **AgentCore Runtime**: Hosts the MCP Server
  - **MCP Server**: Exposes three tools via HTTP transport on port 8000
    - `add_numbers`: Adds two numbers
    - `multiply_numbers`: Multiplies two numbers
    - `greet_user`: Greets a user by name
  - Validates JWT tokens from Cognito
  - Processes MCP tool invocations
- **IAM Roles**: 
  - IAM role for CodeBuild (builds and pushes images)
  - IAM role for AgentCore Runtime (runtime permissions)

## What's Included

This Terraform configuration creates:

- **S3 Bucket**: Stores MCP server source code for version-controlled builds
- **ECR Repository**: Container registry for the MCP server Docker image
- **CodeBuild Project**: Automated Docker image building and pushing
- **Cognito User Pool**: JWT authentication with pre-configured test user
- **Cognito User Pool Client**: Application client for authentication
- **IAM Roles**: Execution roles for AgentCore, CodeBuild, and Cognito operations
- **AgentCore Runtime**: Serverless MCP server runtime with JWT validation

### MCP Server Code Management

The `mcp-server-code/` directory contains your MCP server's source files:
- `mcp_server.py` - MCP server implementation with three tools
- `Dockerfile` - Container configuration
- `requirements.txt` - Python dependencies (mcp>=1.10.0, boto3, bedrock-agentcore)

**Automatic Change Detection**: 
- Terraform archives the `mcp-server-code/` directory
- Uploads to S3 with MD5-based versioning
- CodeBuild pulls from S3 and builds the Docker image
- Any changes to files trigger automatic rebuild (new files, modifications, deletions)

## Prerequisites

### Required Tools

1. **Terraform** (>= 1.6)
   - **Recommended**: [tfenv](https://github.com/tfutils/tfenv) for version management
   - **Or download directly**: [terraform.io/downloads](https://www.terraform.io/downloads)
   
   **Note**: `brew install terraform` provides v1.5.7 (deprecated). Use tfenv or direct download for >= 1.6.

2. **AWS CLI** (configured with credentials)
   ```bash
   aws configure
   ```

3. **Python 3.11+** (for testing scripts)
   ```bash
   python --version  # Verify Python 3.11 or later
   pip install boto3 mcp
   ```

4. **Docker** (for local testing, optional)

### AWS Account Requirements

- AWS Account with appropriate permissions
- Access to Amazon Bedrock AgentCore service
- Permissions to create:
  - ECR repositories
  - CodeBuild projects
  - Cognito User Pools
  - IAM roles and policies
  - AgentCore Runtime resources

## Quick Start

### 1. Configure Variables

Copy the example variables file and customize:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your preferred values.

### 2. Initialize Terraform

See [State Management Options](../README.md#state-management-options) in the main README for detailed guidance on local vs. remote state.

**Quick start with local state:**
```bash
terraform init
```

**For team collaboration, use remote state** - see the [main README](../README.md#state-management-options) for setup instructions.

### 3. Review the Plan

```bash
terraform plan
```

### 4. Deploy

**Method 1: Using Deploy Script (Recommended)**

Make the script executable (first-time only):
```bash
chmod +x deploy.sh
```

Then deploy:
```bash
./deploy.sh
```

The deploy script:
- Validates Terraform configuration
- Shows deployment plan
- Prompts for confirmation
- Applies changes

**Method 2: Direct Terraform Commands**

```bash
terraform apply
```

When prompted, type `yes` to confirm the deployment.

**Note**: The deployment process includes:
1. Creating ECR repository
2. Building Docker image via CodeBuild
3. Creating Cognito User Pool and test user
4. Creating AgentCore Runtime with MCP protocol

Total deployment time: **~5-10 minutes**

### 5. Get Outputs

After deployment completes:

```bash
terraform output
```

Example output:
```
agent_runtime_id = "AGENT1234567890"
agent_runtime_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:agent-runtime/AGENT1234567890"
cognito_user_pool_id = "us-west-2_AbCdEfGhI"
cognito_user_pool_client_id = "1234567890abcdefghijklmno"
cognito_discovery_url = "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_AbCdEfGhI/.well-known/openid-configuration"
test_username = "testuser"
get_token_command = "python get_token.py 1234567890abcdefghijklmno testuser MyPassword123! us-west-2"
```

## Authentication Model

This pattern uses **Cognito JWT-based authentication**:

- **JWT Tokens**: Cognito User Pool issues JWT tokens for authentication
- **Custom JWT Authorizer**: Runtime validates JWT tokens against Cognito discovery URL
- **Test User**: Pre-configured user (testuser/MyPassword123!) for testing
- **Token Expiry**: JWT tokens expire after 1 hour
- **Discovery URL**: OpenID Connect discovery endpoint for token validation

**Authentication Flow:**
1. User authenticates with Cognito User Pool
2. Cognito issues JWT access token
3. Client includes JWT token in MCP request headers
4. Runtime validates token using Cognito's OIDC discovery endpoint
5. Authorized requests processed by MCP server

**Note**: This is a backend authentication pattern for MCP tool access. For user-facing applications, integrate with your identity provider or use Cognito hosted UI for end-user authentication.

## Testing the MCP Server

### Prerequisites for Testing

Before testing, ensure you have the required packages installed:

**Option A: Using uv (Recommended)**
```bash
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install boto3 mcp  # Both required for MCP server testing
```

**Option B: System-wide installation**
```bash
pip install boto3 mcp  # Both required for MCP server testing
```

**Note**: Both `boto3` (for AWS API calls) and `mcp` (for MCP protocol) are required for testing the MCP server.

### Step 1: Get Authentication Token

First, get a JWT token from Cognito:

```bash
# Use the command from terraform outputs
terraform output -raw get_token_command | bash
```

Or manually:

```bash
# Get the Client ID
CLIENT_ID=$(terraform output -raw cognito_user_pool_client_id)
REGION=$(terraform output -raw aws_region)

# Get authentication token
python get_token.py $CLIENT_ID testuser MyPassword123! $REGION
```

This will output a JWT token. Copy the token for the next step.

### Step 2: Test the MCP Server

```bash
# Get the Runtime ARN
RUNTIME_ARN=$(terraform output -raw agent_runtime_arn)
REGION=$(terraform output -raw aws_region)

# Test the MCP server (replace YOUR_JWT_TOKEN with the token from step 1)
python test_mcp_server.py $RUNTIME_ARN YOUR_JWT_TOKEN $REGION
```

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

## Sample Tool Invocations

Try these MCP tool calls:

1. **Add Numbers**:
   ```python
   # Tool: add_numbers
   # Parameters: {"a": 10, "b": 25}
   # Expected Result: 35
   ```

2. **Multiply Numbers**:
   ```python
   # Tool: multiply_numbers
   # Parameters: {"a": 6, "b": 7}
   # Expected Result: 42
   ```

3. **Greet User**:
   ```python
   # Tool: greet_user
   # Parameters: {"name": "John"}
   # Expected Result: "Hello, John! Nice to meet you."
   ```

## Customization

### Modify MCP Server Code

Edit files in `mcp-server-code/` and deploy:

**Adding New Tools**:

Edit `mcp-server-code/mcp_server.py`:
```python
@mcp.tool()
def subtract_numbers(a: int, b: int) -> int:
    """Subtract two numbers"""
    return a - b
```

**Update Dependencies**:

Edit `mcp-server-code/requirements.txt`:
```
mcp>=1.10.0
boto3
bedrock-agentcore
your-new-package>=1.0.0
```

Changes are automatically detected and trigger rebuild. Run `terraform apply` to deploy.

### Modify Authentication

To change Cognito password policy, edit `cognito.tf`:
```hcl
password_policy {
  minimum_length    = 12
  require_uppercase = true
  require_lowercase = true
  require_numbers   = true
  require_symbols   = true
}
```

### Environment Variables

Add to `terraform.tfvars`:
```hcl
environment_variables = {
  LOG_LEVEL = "DEBUG"
  CUSTOM_VAR = "value"
}
```

### Network Mode

Set `network_mode = "PRIVATE"` for VPC deployment (requires additional VPC configuration).

## File Structure

```
mcp-server-agentcore-runtime/
├── main.tf                      # AgentCore runtime with MCP protocol
├── variables.tf                 # Input variables
├── outputs.tf                   # Output values (includes Cognito)
├── versions.tf                  # Provider configuration
├── iam.tf                       # IAM roles and policies
├── s3.tf                        # S3 bucket for MCP server source
├── ecr.tf                       # ECR repository
├── codebuild.tf                 # Docker build automation
├── cognito.tf                   # Cognito User Pool & Client
├── buildspec.yml                # CodeBuild build specification
├── terraform.tfvars.example     # Example configuration
├── backend.tf.example           # Remote state example
├── mcp-server-code/             # MCP server source code
│   ├── mcp_server.py           # MCP server with 3 tools
│   ├── Dockerfile              # Container configuration
│   └── requirements.txt        # Python dependencies
├── scripts/                     # Build automation scripts
│   └── build-image.sh          # CodeBuild trigger & verification
├── get_token.py                 # Cognito JWT token retrieval
├── test_mcp_server.py           # MCP server testing script
├── deploy.sh                    # Deployment helper script
├── destroy.sh                   # Cleanup helper script
├── architecture.png             # Architecture diagram
├── .gitignore                   # Git ignore patterns
└── README.md                    # This file
```

## Troubleshooting

### CodeBuild Fails

If the Docker build fails:

1. Check CodeBuild logs:
   ```bash
   PROJECT_NAME=$(terraform output -raw codebuild_project_name)
   aws codebuild batch-get-builds \
     --ids $PROJECT_NAME
   ```

2. Common issues:
   - Network connectivity issues
   - ECR authentication problems
   - Python dependency conflicts in requirements.txt

### Runtime Creation Fails

If the runtime creation fails:

1. Verify the Docker image exists:
   ```bash
   REPO_NAME=$(terraform output -raw ecr_repository_url | cut -d'/' -f2)
   aws ecr describe-images --repository-name $REPO_NAME
   ```

2. Check IAM role permissions
3. Verify Bedrock AgentCore service quotas
4. Ensure MCP protocol is properly configured

### Authentication Issues

If JWT authentication fails:

1. Verify Cognito user exists:
   ```bash
   USER_POOL_ID=$(terraform output -raw cognito_user_pool_id)
   aws cognito-idp admin-get-user \
     --user-pool-id $USER_POOL_ID \
     --username testuser
   ```

2. Check token expiration (tokens expire after 1 hour)
3. Verify the discovery URL is accessible
4. Ensure allowed_clients matches the client ID

### MCP Server Connection Fails

If MCP tool invocations fail:

1. Check runtime status in AWS Console
2. Review CloudWatch Logs for the runtime
3. Verify JWT token is valid and not expired
4. Check that MCP protocol is configured correctly
5. Ensure the runtime is in ACTIVE state

## Cleanup

### Destroy All Resources

Make the script executable (first-time only):
```bash
chmod +x destroy.sh
```

Then cleanup:
```bash
./destroy.sh
```

Or use Terraform directly:

```bash
terraform destroy
```

**Note**: This will delete:
- AgentCore Runtime
- ECR Repository (and all images)
- Cognito User Pool (and all users)
- S3 Bucket (and all source code archives)
- All IAM roles and policies

### Verify Cleanup

Confirm all resources are deleted:

```bash
# Check AgentCore runtimes
aws bedrock-agentcore list-agent-runtimes

# Check ECR repositories
aws ecr describe-repositories | grep mcp-server

# Check Cognito User Pools
aws cognito-idp list-user-pools --max-results 10
```

## Pricing

For current pricing information, please refer to:
- [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)
- [Amazon ECR Pricing](https://aws.amazon.com/ecr/pricing/)
- [AWS CodeBuild Pricing](https://aws.amazon.com/codebuild/pricing/)
- [Amazon Cognito Pricing](https://aws.amazon.com/cognito/pricing/)
- [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)
- [AWS Lambda Pricing](https://aws.amazon.com/lambda/pricing/)

**Note**: Actual costs depend on your usage patterns, AWS region, and specific services consumed.

## Next Steps

### Explore More Patterns

- [Basic Runtime](../basic-runtime/) - Simpler deployment without MCP protocol
- [Multi-Agent Runtime](../multi-agent-runtime/) - Deploy multiple coordinating agents
- [End-to-End Weather Agent](../end-to-end-weather-agent/) - Full-featured agent with tools

### Extend This Pattern

- Add more MCP tools to `mcp-server-code/mcp_server.py`
- Integrate with external APIs
- Add persistent storage (DynamoDB, S3)
- Implement custom authentication logic
- Add monitoring and alerting
- Deploy to VPC for private networking

### Learn More About MCP

- [Model Context Protocol Specification](https://modelcontextprotocol.io/specification/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Building MCP Servers](https://modelcontextprotocol.io/docs/building-mcp-servers)

## Resources

- [Terraform AWS Provider Documentation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [Amazon Cognito Documentation](https://docs.aws.amazon.com/cognito/)
- [AgentCore Samples Repository](https://github.com/awslabs/agentcore-samples)

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](../../../LICENSE) file for details.
