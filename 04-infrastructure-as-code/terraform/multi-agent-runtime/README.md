# Multi-Agent Runtime on Amazon Bedrock AgentCore (Terraform)

This Terraform module deploys a multi-agent system using Amazon Bedrock AgentCore Runtime with Agent-to-Agent (A2A) communication capabilities.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [What's Included](#whats-included)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Deployment Process](#deployment-process)
- [Authentication Model](#authentication-model)
- [Testing](#testing)
- [Agent Capabilities](#agent-capabilities)
- [Customization](#customization)
- [File Structure](#file-structure)
- [Monitoring and Observability](#monitoring-and-observability)
- [Security](#security)
- [Pricing](#pricing)
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)
- [Advanced Topics](#advanced-topics)
- [Next Steps](#next-steps)
- [Resources](#resources)
- [Contributing](#contributing)
- [License](#license)

## Overview

This pattern demonstrates deploying a multi-agent system with two coordinating agents that communicate via the Agent-to-Agent (A2A) protocol. Agent1 (Orchestrator) can delegate specialized tasks to Agent2 (Specialist), enabling modular and scalable agent architectures.

**Key Features:**
- Two-agent architecture with A2A communication
- Automated Docker image building via CodeBuild
- S3-based source code management with change detection
- IAM-based security with least-privilege access
- Sequential deployment ensuring proper dependencies

This makes it ideal for:
- Building complex multi-agent workflows
- Implementing agent specialization patterns
- Creating scalable agent orchestration systems
- Learning A2A communication protocols

## Architecture

![Multi-Agent Architecture](architecture.png)

### System Components

**Agent1 (Orchestrator Agent)**
- Receives initial user requests
- Orchestrates workflow between multiple agents
- Contains a specialized tool (`call_specialist_agent`) to invoke Agent2
- Has IAM permissions to invoke Agent2's runtime
- Environment variable `AGENT2_ARN` enables A2A communication

**Agent2 (Specialist Agent)**
- Independent specialist agent with domain-specific capabilities
- Provides data analysis and processing functions
- Can be invoked by Agent1 via A2A protocol
- No dependencies on other agents

### Agent-to-Agent (A2A) Communication

The A2A communication pattern enables:
- **Orchestration**: Agent1 coordinates complex workflows
- **Specialization**: Agent2 focuses on specific capabilities
- **Scalability**: Easy to add more specialized agents
- **Security**: IAM-based authorization between agents

## What's Included

This Terraform configuration creates:

- **2 S3 Buckets**: Source code storage for both agents with versioning
- **2 ECR Repositories**: Container registries for ARM64 Docker images
- **2 CodeBuild Projects**: Automated image building and pushing
- **3 IAM Roles**: 
  - Agent1 execution role (with A2A permissions)
  - Agent2 execution role (standard permissions)
  - CodeBuild service role
- **2 Agent Runtimes**: 
  - Agent1 (Orchestrator) with AGENT2_ARN environment variable
  - Agent2 (Specialist) independent runtime
- **Build Automation**: Automatic rebuild on code changes (MD5-based detection)
- **Supporting Resources**: S3 lifecycle policies, ECR lifecycle policies, IAM policies

**Total:** ~30 AWS resources deployed and managed by Terraform

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
   pip install boto3
   ```

4. **Docker** (for local testing, optional)

### AWS Account Requirements

- AWS Account with appropriate permissions
- Access to Amazon Bedrock AgentCore service
- Permissions to create:
  - S3 buckets
  - ECR repositories
  - CodeBuild projects
  - IAM roles and policies
  - AgentCore Runtime resources

## Quick Start

### 1. Configure Variables

Copy the example variables file and customize:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your preferred values:
- `orchestrator_name`: Name for the orchestrator agent (default: "OrchestratorAgent")
- `specialist_name`: Name for the specialist agent (default: "SpecialistAgent")
- `stack_name`: Stack identifier (default: "agentcore-multi-agent")
- `aws_region`: AWS region for deployment (default: "us-west-2")
- `network_mode`: PUBLIC or PRIVATE networking

### 2. Initialize Terraform

See [State Management Options](../README.md#state-management-options) in the main README for detailed guidance on local vs. remote state.

**Quick start with local state:**
```bash
terraform init
```

**For team collaboration, use remote state** - see the [main README](../README.md#state-management-options) for setup instructions.

### 3. Deploy

**Method 1: Using Deploy Script (Recommended)**

```bash
chmod +x deploy.sh
./deploy.sh
```

The script validates configuration, shows the plan, and deploys all resources.

**Method 2: Direct Terraform Commands**

```bash
terraform plan
terraform apply
```

**Note**: Deployment includes creating infrastructure, building Docker images sequentially (Agent2 first, then Agent1), and establishing A2A communication. Total deployment time: **~5-10 minutes**

### 4. Verify Deployment

```bash
# View all outputs
terraform output

# Get Agent ARNs
terraform output orchestrator_runtime_arn
terraform output specialist_runtime_arn
```

## Deployment Process

### Sequential Build Process

The deployment follows a strict sequence to ensure proper dependencies:

```
1. S3 Buckets Creation (orchestrator & specialist)
2. ECR Repositories Creation (orchestrator & specialist)
3. IAM Roles Creation (with A2A permissions)
4. CodeBuild Projects Creation (orchestrator & specialist)
5. Agent2 Docker Build → Agent2 Runtime Creation
6. Agent1 Docker Build → Agent1 Runtime Creation (depends on Agent2)
```

**Critical Dependencies:**
- Agent1 runtime depends on Agent2 runtime being created first
- Agent1 build depends on Agent2 build completing successfully
- Agent1 receives `AGENT2_ARN` as an environment variable

### Build Triggers

The infrastructure automatically triggers Docker image builds:
- When source code changes (MD5 hash detection)
- When infrastructure changes require rebuild
- Sequential: Agent2 builds first, then Agent1

## Authentication Model

This pattern uses **IAM-based authentication with workload identity tokens**:

- **Service Principal**: Agents assume IAM roles via `bedrock-agentcore.amazonaws.com`
- **Workload Identity**: Agents obtain access tokens for secure operations
- **A2A Authorization**: Agent1 has `InvokeAgentRuntime` permission for Agent2
- **API Access**: Direct AWS API invocation using IAM credentials

**Note**: This is a backend infrastructure pattern with no user authentication layer. For user-facing applications, you would add Cognito or API Gateway authorizers separately.

## Testing

The included `test_multi_agent.py` script is **infrastructure-agnostic** and works with any deployment method (Terraform, CDK, CloudFormation, or manual).

### Prerequisites for Testing

Before testing, ensure you have the required packages installed:

**Option A: Using uv (Recommended)**
```bash
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
uv pip install boto3  # Required for agent invocation
```

**Option B: System-wide installation**
```bash
pip install boto3  # Required for agent invocation
```

**Note**: `boto3` is required for the test script to invoke both agent runtimes via AWS API.

### Basic Testing

```bash
# Get ARNs from Terraform
ORCHESTRATOR_ARN=$(terraform output -raw orchestrator_runtime_arn)
SPECIALIST_ARN=$(terraform output -raw specialist_runtime_arn)

# Test both agents
python test_multi_agent.py $ORCHESTRATOR_ARN $SPECIALIST_ARN
```

### Test Scenarios

The script runs two tests:
1. **Simple Query**: Basic orchestrator invocation
2. **A2A Communication**: Orchestrator delegates to specialist via A2A protocol

### Expected Output

```
TEST 1: Simple Query (Orchestrator) ✅
TEST 2: Complex Query with A2A Communication ✅

✅ ALL TESTS PASSED
```

## Agent Capabilities

### Agent1 (Orchestrator)

**Tools:**
- `call_specialist_agent`: Invokes Agent2 for specialized processing
  - Parameters: `query` (string)
  - Returns: Processed results from Agent2

**Use Cases:**
- Complex workflow orchestration
- Multi-step data processing
- Delegation to specialized agents

### Agent2 (Specialist)

**Capabilities:**
- Domain-specific data analysis
- Detailed information processing
- Expert-level responses

**Use Cases:**
- Data analysis and transformation
- Domain-specific processing
- Specialized computations

## Customization

### Modify Agent Code

1. **Edit Agent Files**
   ```bash
   # Orchestrator Agent
   vim agent-orchestrator-code/agent.py
   vim agent-orchestrator-code/requirements.txt

   # Specialist Agent
   vim agent-specialist-code/agent.py
   vim agent-specialist-code/requirements.txt
   ```

2. **Redeploy**
   ```bash
   terraform apply  # Automatically detects changes and rebuilds
   ```

### Add More Agents

To add a new agent (e.g., Coordinator):
1. Create `coordinator-code/` directory with implementation
2. Add `coordinator.tf` for the runtime resource
3. Update `s3.tf`, `ecr.tf`, `iam.tf`, `codebuild.tf`
4. Create `buildspec-coordinator.yml`
5. Update `main.tf` for build sequence
6. Update `outputs.tf` and `variables.tf`

### Modify Network Configuration

Change from PUBLIC to PRIVATE networking:

```hcl
# terraform.tfvars
network_mode = "PRIVATE"
```

Requires VPC configuration (not included in this module).

## File Structure

```
multi-agent-runtime/
├── agent-orchestrator-code/           # Orchestrator agent source code
│   ├── agent.py                 # Main agent implementation
│   ├── Dockerfile               # Container definition
│   └── requirements.txt         # Python dependencies
├── agent-specialist-code/             # Specialist agent source code
│   ├── agent.py                 # Main agent implementation
│   ├── Dockerfile               # Container definition
│   └── requirements.txt         # Python dependencies
├── orchestrator.tf              # Orchestrator runtime configuration
├── specialist.tf                # Specialist runtime configuration
├── main.tf                      # Main Terraform configuration
├── variables.tf                 # Input variables
├── outputs.tf                   # Output definitions
├── iam.tf                       # IAM roles and policies
├── s3.tf                        # S3 buckets for source code
├── ecr.tf                       # ECR repositories
├── codebuild.tf                 # CodeBuild projects
├── versions.tf                  # Terraform and provider versions
├── buildspec-orchestrator.yml   # Orchestrator build specification
├── buildspec-specialist.yml     # Specialist build specification
├── terraform.tfvars.example     # Example variable values
├── backend.tf.example           # Example backend configuration
├── deploy.sh                    # Deployment automation script
├── destroy.sh                   # Cleanup automation script
├── test_multi_agent.py          # Infrastructure-agnostic test script
└── README.md                    # This file
```

## Monitoring and Observability

### CloudWatch Logs

```bash
# Orchestrator logs
aws logs tail /aws/bedrock-agentcore/agentcore-multi-agent-orchestrator-runtime --follow

# Specialist logs
aws logs tail /aws/bedrock-agentcore/agentcore-multi-agent-specialist-runtime --follow
```

### Metrics

Access metrics in CloudWatch:
- Agent invocation count
- Agent execution duration
- Error rates
- A2A call metrics

### AWS Console

Monitor in AWS Console:
- **Bedrock AgentCore**: [Console Link](https://console.aws.amazon.com/bedrock/home#/agentcore)
- **ECR Repositories**: View Docker images
- **CodeBuild**: Monitor build status
- **CloudWatch**: View logs and metrics

## Security

### IAM Permissions

**Agent1 Execution Role:**
- Standard AgentCore permissions
- **Critical**: `bedrock-agentcore:InvokeAgentRuntime` for Agent2

**Agent2 Execution Role:**
- Standard AgentCore permissions only
- No cross-agent invocation permissions needed

**CodeBuild Role:**
- S3 access to both agent source buckets
- ECR push access to both repositories
- CloudWatch Logs write access

### Network Security

- Agents run in specified network mode (PUBLIC/PRIVATE)
- ECR repositories have account-level access controls
- S3 buckets block public access
- IAM policies follow least-privilege principle

### Secrets Management

For sensitive data:
- Use AWS Secrets Manager
- Pass secret ARNs as environment variables
- Retrieve secrets at runtime in agent code

## Pricing

For current pricing information, please refer to:
- [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/)
- [Amazon ECR Pricing](https://aws.amazon.com/ecr/pricing/)
- [AWS CodeBuild Pricing](https://aws.amazon.com/codebuild/pricing/)
- [Amazon S3 Pricing](https://aws.amazon.com/s3/pricing/)
- [Amazon CloudWatch Pricing](https://aws.amazon.com/cloudwatch/pricing/)

**Note**: Actual costs depend on your usage patterns, AWS region, and specific services consumed.

## Troubleshooting

### Common Issues

**Issue**: Agent1 fails to invoke Agent2
- **Solution**: Verify AGENT2_ARN environment variable is set
- **Check**: IAM permissions include InvokeAgentRuntime

**Issue**: Build fails
- **Solution**: Check CodeBuild logs in CloudWatch
- **Check**: Verify source code is in correct directories

**Issue**: Runtime not created
- **Solution**: Verify ECR image exists and is tagged correctly
- **Check**: Review Terraform state for errors

### Debug Commands

```bash
# Check Terraform state
terraform show

# Validate configuration
terraform validate

# View specific resource
terraform state show aws_bedrockagentcore_agent_runtime.orchestrator

# Get detailed build logs
PROJECT_NAME=$(terraform output -raw orchestrator_codebuild_project)
aws codebuild batch-get-builds --ids $(aws codebuild list-builds-for-project --project-name $PROJECT_NAME --query 'ids[0]' --output text)
```

## Cleanup

### Automated Cleanup

```bash
chmod +x destroy.sh
./destroy.sh
```

The script shows the destruction plan, requires confirmation, and destroys all resources.

### Manual Cleanup

```bash
terraform destroy
```

**Important**: Verify in AWS Console that all resources are deleted:
- Bedrock AgentCore runtimes
- ECR repositories
- S3 buckets
- CodeBuild projects
- IAM roles

## Advanced Topics

### Adding Custom Tools

1. Define tool schema in agent code
2. Implement tool handler function
3. Register tool with agent
4. Rebuild and deploy

### Implementing Memory

Add session management in agent code:
```python
session_data = {}

def handle_request(input_text, session_id):
    if session_id not in session_data:
        session_data[session_id] = {}
    # Use session_data for context
```

### Multi-Region Deployment

For multi-region:
1. Configure backend for state locking
2. Deploy to each region separately
3. Use Route53 for failover
4. Consider cross-region replication for S3/ECR

## Next Steps

1. **Test the deployment**
   ```bash
   python test_multi_agent.py $(terraform output -raw orchestrator_runtime_arn) $(terraform output -raw specialist_runtime_arn)
   ```

2. **Customize agents** for your specific use case
   - Add domain-specific tools to agents
   - Implement custom business logic
   - Integrate with external APIs

3. **Explore related patterns**
   - [MCP Server Pattern](../mcp-server-agentcore-runtime/) - MCP protocol with JWT auth
   - [AgentCore Samples](https://github.com/awslabs/agentcore-samples) - More examples

4. **Add production features**
   - Monitoring and alerting
   - Custom authentication layer (if needed)
   - VPC deployment for private networking
   - CI/CD pipeline integration

## Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Agent-to-Agent Communication](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-a2a.html)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## Contributing

We welcome contributions! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT-0 license. See the [LICENSE](../../../LICENSE) file for details.
