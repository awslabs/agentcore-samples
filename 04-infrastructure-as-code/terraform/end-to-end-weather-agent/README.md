# End-to-End Weather Agent on Amazon Bedrock AgentCore (Terraform)

This Terraform module deploys a comprehensive weather agent using Amazon Bedrock AgentCore Runtime with integrated AgentCore tools (Browser, Code Interpreter, and Memory).

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
- [Troubleshooting](#troubleshooting)
- [Cleanup](#cleanup)
- [Advanced Topics](#advanced-topics)
- [Next Steps](#next-steps)
- [Resources](#resources)
- [Contributing](#contributing)
- [License](#license)

## Overview

This pattern demonstrates deploying a full-featured weather agent with Amazon Bedrock AgentCore tools, enabling sophisticated weather intelligence capabilities through web browsing, code execution, and persistent memory.

**Key Features:**
- Integrated AgentCore tools (Browser, Code Interpreter, Memory)
- Automated Docker image building via CodeBuild
- S3-based artifact storage for analysis results
- IAM-based security with tool-specific permissions
- Weather data visualization and analysis capabilities

This makes it ideal for:
- Building intelligent weather information systems
- Creating data analysis pipelines with code execution
- Implementing web scraping for real-time weather data
- Learning AgentCore tool integration patterns

## Architecture

![Weather Agent Architecture](architecture.png)

### System Components

**Weather Agent Runtime**
- Processes natural language weather queries
- Coordinates multiple tools for comprehensive responses
- Generates visualizations and detailed analyses
- Maintains conversation context across sessions

**AgentCore Tools**

1. **Browser Tool**
   - Accesses weather websites and advisories
   - Scrapes real-time weather data
   - Retrieves alerts and warnings
   - Checks weather-related news

2. **Code Interpreter Tool**
   - Executes Python for data analysis
   - Creates weather visualizations (charts, graphs)
   - Performs statistical calculations
   - Processes and transforms weather data

3. **Memory**
   - Stores conversation history
   - Remembers user preferences
   - Maintains context across sessions
   - Event expiry: 30 days

### Tool Integration

The agent seamlessly coordinates all tools:
- **Query Processing**: Understands weather-related requests
- **Tool Selection**: Chooses appropriate tools automatically
- **Data Analysis**: Uses Code Interpreter for complex calculations
- **Web Access**: Employs Browser for real-time information
- **Context Retention**: Leverages Memory for personalized responses

## What's Included

This Terraform configuration creates:

- **2 S3 Buckets**: 
  - Source code storage with versioning
  - Results bucket for analysis artifacts
- **1 ECR Repository**: Container registry for ARM64 Docker image
- **1 CodeBuild Project**: Automated image building and pushing
- **2 IAM Roles**: 
  - Agent execution role (with tool permissions)
  - CodeBuild service role
- **1 Agent Runtime**: Weather agent with tool integration
- **3 AgentCore Tools**:
  - Browser for web access
  - Code Interpreter for analysis
  - Memory for context retention
- **Build Automation**: Automatic rebuild on code changes (MD5-based detection)
- **Supporting Resources**: S3 lifecycle policies, ECR lifecycle policies, IAM policies

**Total:** ~20 AWS resources deployed and managed by Terraform

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

3. **Python 3.11+** (for testing scripts and memory initialization)
   ```bash
   python --version  # Verify Python 3.11 or later
   pip install boto3
   ```
   
   **Note**: `boto3` is required for:
   - Running test scripts (`test_weather_agent.py`)
   - Automatic memory initialization during deployment

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
- `agent_name`: Name for the weather agent (default: "WeatherAgent")
- `memory_name`: Name for memory resource (default: "WeatherAgentMemory")
- `stack_name`: Stack identifier (default: "agentcore-weather")
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

**Note**: Deployment includes creating infrastructure, building the Docker image, and provisioning all AgentCore tools. Total deployment time: **~8-12 minutes**

### 4. Verify Deployment

```bash
# View all outputs
terraform output

# Get Agent and Tool IDs
terraform output agent_runtime_arn
terraform output browser_id
terraform output code_interpreter_id
terraform output memory_id
```

## Deployment Process

### Build and Deployment Sequence

The deployment follows this sequence:

```
1. S3 Buckets Creation (source & results)
2. ECR Repository Creation
3. IAM Roles Creation (with tool permissions)
4. AgentCore Tools Creation (Browser, Code Interpreter, Memory)
5. CodeBuild Project Creation
6. Docker Image Build
7. Weather Agent Runtime Creation (with tool IDs as environment variables)
```

**Tool Integration:**
- Agent receives tool IDs as environment variables
- IAM role grants permissions for tool operations
- Tools are created before agent runtime
- Results bucket for code interpreter outputs

### Build Triggers

The infrastructure automatically triggers Docker image builds:
- When source code changes (MD5 hash detection)
- When infrastructure changes require rebuild
- Build typically takes 5-8 minutes

### Memory Initialization

Memory is automatically initialized with activity preferences (good/ok/poor weather activities) via `scripts/init-memory.py` during deployment. The agent uses these preferences for weather-based activity recommendations.

### Observability

Full observability is automatically configured with CloudWatch Logs (14-day retention) and X-Ray traces. Logs are delivered via vended logs delivery to `/aws/vendedlogs/bedrock-agentcore/${runtime_id}`. Access via CloudWatch Console or `aws logs tail` command.

## Authentication Model

This pattern uses **IAM-based authentication with workload identity tokens**:

- **Service Principal**: Agent assumes IAM role via `bedrock-agentcore.amazonaws.com`
- **Workload Identity**: Agent obtains access tokens for secure operations
- **Tool Access**: IAM permissions for Browser, Code Interpreter, and Memory
- **S3 Access**: Agent can write analysis results to S3 bucket

**Note**: This is a backend infrastructure pattern with no user authentication layer. For user-facing applications, you would add Cognito or API Gateway authorizers separately.

## Testing

The included `test_weather_agent.py` script is **infrastructure-agnostic** and works with any deployment method (Terraform, CDK, CloudFormation, or manual).

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

**Note**: `boto3` is required for the test script to invoke the agent runtime via AWS API.

### Basic Testing

```bash
# Get ARN from Terraform
AGENT_ARN=$(terraform output -raw agent_runtime_arn)

# Test the agent
python test_weather_agent.py $AGENT_ARN
```

### Test Scenarios

The script runs two comprehensive tests:
1. **Simple Weather Query**: Basic weather information request
2. **Complex Query with Tools**: Demonstrates browser, code interpreter, and memory usage together

### Expected Output

```
TEST 1: Simple Weather Query ✅
TEST 2: Complex Query with Tools ✅

✅ ALL TESTS PASSED
```

## Agent Capabilities

### Weather Agent

**Core Capabilities:**
- Natural language weather queries
- Real-time weather data access
- Historical weather analysis
- Weather forecasting insights
- Multi-location comparisons
- Travel weather planning

**Integrated Tools:**

1. **Browser Tool**
   - Access weather.gov, weather.com, and other weather sites
   - Retrieve current conditions and forecasts
   - Check weather alerts and warnings
   - Gather radar and satellite imagery links

2. **Code Interpreter Tool**
   - Analyze temperature trends
   - Create weather visualizations (line charts, bar graphs)
   - Calculate statistics (averages, extremes)
   - Process historical weather data
   - Generate custom weather reports

3. **Memory**
   - Remember user location preferences
   - Track frequently requested locations
   - Maintain conversation context
   - Store user preferences for units (F/C)
   - Pre-initialized with activity preferences for weather-based recommendations

### Example Interactions

**Simple Query:**
```
User: "What's the weather like in Seattle?"
Agent: [Uses Browser] Provides current conditions, forecast, temperature
```

**Analysis Query:**
```
User: "Compare temperatures between New York and Miami over the past week"
Agent: [Uses Browser + Code Interpreter] Fetches data, creates comparison chart
```

**Planning Query:**
```
User: "I'm planning a road trip from Boston to Miami next week. What should I expect?"
Agent: [Uses Browser + Memory] Provides route-based weather forecast, remembers trip details
```

## Customization

### Modify Agent Code

1. **Edit Agent Files**
   ```bash
   vim agent-code/weather_agent.py
   vim agent-code/requirements.txt
   vim agent-code/Dockerfile
   ```

2. **Redeploy**
   ```bash
   terraform apply  # Automatically detects changes and rebuilds
   ```

### Add Additional AgentCore Tools

To add more tools:
1. Create new tool resource file (e.g., `gateway.tf`)
2. Add tool ID to agent environment variables in `main.tf`
3. Update IAM permissions in `iam.tf` if needed
4. Update `outputs.tf` with new tool outputs

### Modify Network Configuration

Change from PUBLIC to PRIVATE networking:

```hcl
# terraform.tfvars
network_mode = "PRIVATE"
```

Requires VPC configuration (not included in this module).

### Customize Memory Settings

Adjust memory retention period:

```hcl
# memory.tf
event_expiry_duration = 60  # Change from 30 to 60 days
```

## File Structure

```
end-to-end-weather-agent/
├── agent-code/               # Weather agent source code
│   ├── weather_agent.py      # Agent implementation
│   ├── requirements.txt      # Python dependencies
│   └── Dockerfile            # Container definition
├── scripts/                  # Build automation and initialization
│   ├── build-image.sh        # Docker build script
│   └── init-memory.py        # Memory initialization script
├── main.tf                   # Agent runtime configuration
├── browser.tf                # Browser tool
├── code_interpreter.tf       # Code interpreter tool
├── memory.tf                 # Memory resource
├── memory-init.tf            # Memory initialization automation
├── observability.tf          # CloudWatch Logs and X-Ray traces
├── iam.tf                    # IAM roles and policies
├── s3.tf                     # S3 buckets
├── ecr.tf                    # ECR repository
├── codebuild.tf              # CodeBuild project
├── outputs.tf                # Output values
├── variables.tf              # Input variables
├── versions.tf               # Provider versions
├── buildspec.yml             # CodeBuild specification
├── test_weather_agent.py     # Infrastructure-agnostic test script
├── deploy.sh                 # Deployment automation
├── destroy.sh                # Cleanup automation
├── terraform.tfvars.example  # Example configuration
├── backend.tf.example        # Remote state example
├── .gitignore                # Git exclusions
├── architecture.png          # Architecture diagram
└── README.md                 # This file
```

## Monitoring and Observability

### CloudWatch Logs (Automatic)

Terraform automatically creates CloudWatch Log Group with vended logs delivery:

```bash
# Get log group name
LOG_GROUP=$(terraform output -raw log_group_name)

# Tail agent logs
aws logs tail $LOG_GROUP --follow

# CodeBuild logs
aws logs tail /aws/codebuild/agentcore-weather-agent-build --follow
```

### X-Ray Traces (Automatic)

Distributed tracing automatically delivered to X-Ray. View traces in [X-Ray Console](https://console.aws.amazon.com/xray/home).

### Metrics

Access metrics in CloudWatch:
- Agent invocation count
- Tool usage frequency (Browser, Code Interpreter, Memory)
- Agent execution duration
- Error rates
- Code interpreter execution time

### AWS Console

Monitor in AWS Console:
- **CloudWatch Logs**: Vended logs at `/aws/vendedlogs/bedrock-agentcore/${runtime_id}`
- **X-Ray**: Distributed request traces
- **Bedrock AgentCore**: [Console Link](https://console.aws.amazon.com/bedrock/home#/agentcore)
- **ECR Repository**: Docker images
- **CodeBuild**: Build status
- **S3 Results Bucket**: Generated artifacts

## Security

### IAM Permissions

**Agent Execution Role:**
- Standard AgentCore permissions
- ECR image pull access
- S3 read/write for results bucket
- CloudWatch Logs write access
- Tool-specific permissions (Browser, Code Interpreter, Memory access)

**CodeBuild Role:**
- S3 access to source bucket
- ECR push access
- CloudWatch Logs write access

### Network Security

- Agent runs in specified network mode (PUBLIC/PRIVATE)
- ECR repository has account-level access controls
- S3 buckets block public access
- IAM policies follow least-privilege principle
- Tool resources use network isolation

### Secrets Management

For sensitive data:
- Use AWS Secrets Manager
- Pass secret ARNs as environment variables
- Retrieve secrets at runtime in agent code

## Troubleshooting

### Common Issues

**Issue**: Agent cannot access tools
- **Solution**: Verify tool IDs are set as environment variables
- **Check**: IAM permissions include tool access

**Issue**: Code Interpreter fails
- **Solution**: Check CloudWatch logs for Python errors
- **Check**: Verify results bucket permissions

**Issue**: Browser tool times out
- **Solution**: Check network connectivity
- **Check**: Verify target websites are accessible

**Issue**: Build fails
- **Solution**: Check CodeBuild logs in CloudWatch
- **Check**: Verify source code is in correct directory

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
terraform state show aws_bedrockagentcore_agent_runtime.agent

# Get tool IDs
terraform output browser_id
terraform output code_interpreter_id
terraform output memory_id

# Check results bucket
aws s3 ls s3://$(terraform output -raw results_bucket_name)/

# Get detailed build logs
PROJECT_NAME=$(terraform output -raw agent_codebuild_project_name)
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
   python test_weather_agent.py $(terraform output -raw agent_runtime_arn)
   ```

2. **Customize the agent** for your specific use case
   - Modify weather data sources
   - Add custom weather analysis logic
   - Integrate additional external APIs
   - Enhance visualization capabilities

3. **Explore related patterns**
   - [Multi-Agent Runtime](../multi-agent-runtime/) - Agent-to-Agent communication
   - [MCP Server Pattern](../mcp-server-agentcore-runtime/) - MCP protocol with JWT auth
   - [AgentCore Samples](https://github.com/awslabs/agentcore-samples) - More examples

4. **Add production features**
   - Monitoring and alerting
   - Custom authentication layer
   - VPC deployment for private networking
   - CI/CD pipeline integration
   - Rate limiting and throttling

## Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Agent-to-Agent Communication](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-a2a.html)
- [Model Context Protocol](https://modelcontextprotocol.io/)

## Contributing

We welcome contributions! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.

## License

This project is licensed under the MIT-0 license. See the [LICENSE](../../../LICENSE) file for details.
