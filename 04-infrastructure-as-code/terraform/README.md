# Terraform Samples

Deploy Amazon Bedrock AgentCore resources using Terraform.

## Prerequisites

- **Terraform >= 1.6** 
  - **Recommended**: [tfenv](https://github.com/tfutils/tfenv) for version management
  - **Or download directly**: [terraform.io/downloads](https://www.terraform.io/downloads)
  - **Note**: `brew install terraform` provides v1.5.7 (deprecated). Use tfenv or direct download for >= 1.6
- **AWS CLI** configured with credentials
- **Python 3.11+** (for testing scripts)
- **Docker** (optional, for local testing)
- Access to Amazon Bedrock AgentCore (preview)

## State Management Options

Terraform tracks deployed resources in a state file. Choose the approach that fits your needs:

### Option A: Local State (Quickstart)

Perfect for testing, learning, and solo development:

```bash
cd <sample-directory>
terraform init
```

**Characteristics:**
- State stored in local `terraform.tfstate` file
- Simple setup, no additional configuration
- Best for individual experimentation
- Not suitable for team collaboration

### Option B: Remote State (Teams/Production)

Recommended for team collaboration and production environments:

```bash
cd <sample-directory>

# 1. Setup (one-time per pattern)
cp backend.tf.example backend.tf
# Edit backend.tf with your S3 bucket and DynamoDB table

# 2. Initialize with backend
terraform init
```

**Characteristics:**
- State stored in S3 with DynamoDB locking
- Enables team collaboration
- Provides state versioning and backup
- Prevents concurrent modifications

**Setup Requirements:**
- S3 bucket for state storage
- DynamoDB table for state locking
- See `backend.tf.example` in each pattern for details

💡 **Note**: You must create the S3 bucket and DynamoDB table before running `terraform init` with remote state. See `backend.tf.example` in each pattern directory for setup instructions.

## General Deployment Pattern

### Option 1: Using Automation Scripts (Recommended)

```bash
cd <sample-directory>
chmod +x deploy.sh
./deploy.sh
```

The deployment script will:
- Validate your environment
- Initialize Terraform
- Create and review the plan
- Deploy all resources
- Display outputs and next steps

### Option 2: Manual Terraform Commands

```bash
cd <sample-directory>

# 1. Configure variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 2. Choose state management (see State Management Options above)
terraform init

# 3. Review the plan
terraform plan

# 4. Deploy
terraform apply

# 5. View outputs
terraform output
```

## Testing

All patterns include Python test scripts to verify your deployment.

### Setup Test Environment

**Option 1: Using uv (Recommended)**

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Activate virtual environment
source .venv/bin/activate  # On macOS/Linux
# .venv\Scripts\activate   # On Windows

# Install boto3
uv pip install boto3
```

**Option 2: Using pip**

```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # On macOS/Linux
# .venv\Scripts\activate   # On Windows

# Install boto3
pip install boto3
```

### Run Tests

```bash
# Get the agent ARN from Terraform outputs
AGENT_ARN=$(terraform output -raw agent_runtime_arn)

# Run the test script
python test_*.py $AGENT_ARN
```

### Cleanup

```bash
# Using automation script
./destroy.sh

# Or using Terraform directly
terraform destroy
```

## Samples

- **[basic-runtime/](./basic-runtime/)** - Simple agent deployment with container runtime
- **[mcp-server-agentcore-runtime/](./mcp-server-agentcore-runtime/)** - MCP Server with JWT authentication and API Gateway
- **[multi-agent-runtime/](./multi-agent-runtime/)** - Multi-agent system with Agent-to-Agent (A2A) communication
- **[end-to-end-weather-agent/](./end-to-end-weather-agent/)** - Weather agent with Browser, Code Interpreter, and Memory tools
- **[web-search-gateway/](./web-search-gateway/)** - AgentCore Gateway with the managed Web Search connector and domain governance (no agent runtime)

## Terraform Advantages

- **Infrastructure as Code**: Define resources declaratively with HCL
- **State Management**: Track and manage infrastructure state
- **Module Reusability**: Create reusable infrastructure components
- **Plan Before Apply**: Preview changes before deployment
- **Automated Image Building**: Uses CodeBuild for Docker image creation
- **Provider Ecosystem**: Access to thousands of providers and resources
- **Automation Scripts**: Included deploy.sh and destroy.sh for easy deployment

## Pattern Comparison

| Pattern | Agent Runtimes | Tools | A2A | MCP Server | Use Case |
|---------|----------------|-------|-----|------------|----------|
| basic-runtime | 1 | - | ❌ | ❌ | Simple agent deployment |
| mcp-server | 1 | - | ❌ | ✅ | API integration with JWT auth |
| multi-agent | 2 | - | ✅ | ❌ | Orchestrator + Specialist pattern |
| weather-agent | 1 | Browser, Code Interpreter, Memory | ❌ | ❌ | Full-featured agent with tools |
| web-search-gateway | 0 | Web Search (managed connector) | ❌ | ✅ | Governed web grounding for any MCP client |

## Troubleshooting

### Terraform Version Issues

If you encounter provider compatibility issues:

```bash
# Install specific Terraform version with tfenv
tfenv install 1.6.0
tfenv use 1.6.0
```

### State Management

```bash
# View current state
terraform show

# List all resources in state
terraform state list

# Remove a resource from state (if needed)
terraform state rm <resource_address>
```

### Provider Errors

If you see provider version conflicts:

```bash
# Upgrade providers to latest compatible versions
terraform init -upgrade

# Lock provider versions
terraform providers lock
```

### CodeBuild Failures

Check build logs:

```bash
# Get project name from outputs
PROJECT_NAME=$(terraform output -raw codebuild_project_name)

# View recent build logs
aws codebuild list-builds-for-project \
  --project-name $PROJECT_NAME \
  --region <region>

# Get specific build details
aws codebuild batch-get-builds \
  --ids <build-id> \
  --region <region>
```

### Deployment Stuck

If deployment appears stuck:

```bash
# Check CloudWatch Logs for the agent runtime
aws logs tail /aws/bedrock-agentcore/<runtime-name> --follow

# Check CodeBuild progress
aws codebuild list-builds-for-project \
  --project-name <project-name> \
  --max-items 5
```

### Resource Already Exists

If you encounter "resource already exists" errors:

```bash
# Import existing resource into state
terraform import <resource_type>.<resource_name> <resource_id>

# Example for S3 bucket
terraform import aws_s3_bucket.example my-bucket-name
```

### Cleanup Issues

If `terraform destroy` fails:

```bash
# Manually empty S3 buckets first
aws s3 rm s3://<bucket-name> --recursive

# Force destroy (use with caution)
terraform destroy -auto-approve

# Check for remaining resources
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=ManagedBy,Values=Terraform
```

## Key Features

### State Management

Terraform tracks all deployed resources in a state file. For team collaboration:

```bash
# Setup remote state (example with S3)
cp backend.tf.example backend.tf
# Edit backend.tf with your S3 bucket details
terraform init -migrate-state
```

### Automated Docker Builds

Each pattern uses AWS CodeBuild to automatically build ARM64 Docker images:
- Triggered on source code changes (MD5 hash detection)
- No local Docker daemon required
- Optimized for AWS Graviton processors

### Testing Scripts

All patterns include infrastructure-agnostic Python test scripts:

```bash
# Get the agent ARN from Terraform outputs
AGENT_ARN=$(terraform output -raw agent_runtime_arn)

# Run tests
python test_*.py $AGENT_ARN
```

## Additional Resources

- [Terraform Documentation](https://www.terraform.io/docs)
- [AWS Provider Documentation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [Terraform Best Practices](https://www.terraform.io/docs/cloud/guides/recommended-practices/index.html)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT-0 license. See the [LICENSE](../../LICENSE) file for details.
