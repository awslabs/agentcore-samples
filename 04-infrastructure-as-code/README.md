# Infrastructure as Code Samples for Amazon Bedrock AgentCore

Deploy Amazon Bedrock AgentCore resources using CloudFormation templates, AWS CDK, or Terraform.

## Overview

These Infrastructure as Code samples enable you to:
- Deploy AgentCore resources consistently across environments
- Automate infrastructure provisioning with Infrastructure as Code
- Maintain version control of your infrastructure
- Implement AWS best practices for security and monitoring

Choose your preferred approach:
- **[CloudFormation](./cloudformation/)** - YAML/JSON templates for declarative infrastructure
- **[CDK](./cdk/)** - Python or TypeScript code for programmatic infrastructure
- **[Terraform](./terraform/)** - HCL code for declarative infrastructure with state management

## Samples

### 1. Basic Agent Runtime
Deploy a simple AgentCore Runtime with a basic Strands agent - no additional tools or memory.

**What it deploys:**
- AgentCore Runtime with simple agent
- ECR Repository and automated Docker builds
- IAM roles with least-privilege policies

**Use case:** Learning AgentCore basics without complexity  
**Deployment time:** ~5-15 minutes  
**Estimated cost:** ~$50-100/month

**Implementation:** [CloudFormation](./cloudformation/basic-runtime/) | [CDK](./cdk/python/basic-runtime/) | [Terraform](./terraform/basic-runtime/)

### 2. MCP Server on AgentCore Runtime
Deploy a complete MCP (Model Context Protocol) server with automated Docker building and JWT authentication.

**What it deploys:**
- AgentCore Runtime hosting MCP server
- Amazon Cognito for JWT authentication
- Automated ARM64 Docker builds

**Sample MCP Tools:** `add_numbers`, `multiply_numbers`, `greet_user`  
**Deployment time:** ~10-15 minutes  
**Estimated cost:** ~$50-100/month

**Implementation:** [CloudFormation](./cloudformation/mcp-server-agentcore-runtime/) | [CDK](./cdk/python/mcp-server-agentcore-runtime/) | [Terraform](./terraform/mcp-server-agentcore-runtime/)

### 3. Multi-Agent Runtime
Deploy a multi-agent system where Agent1 (orchestrator) can invoke Agent2 (specialist) for complex tasks.

**What it deploys:**
- Two AgentCore Runtimes with agent-to-agent communication
- IAM roles with agent-to-agent invocation permissions
- Separate ECR repositories for each agent

**Architecture:** Agent1 routes requests and delegates to Agent2 for detailed analysis  
**Deployment time:** ~15-20 minutes  
**Estimated cost:** ~$100-200/month

**Implementation:** [CloudFormation](./cloudformation/multi-agent-runtime/) | [CDK](./cdk/python/multi-agent-runtime/) | [Terraform](./terraform/multi-agent-runtime/)

### 4. End-to-End Weather Agent with Tools and Memory
Deploy a complete weather-based activity planning agent with browser automation, code interpreter, and memory.

**What it deploys:**
- AgentCore Runtime with Strands agent
- Browser Tool for web scraping weather data
- Code Interpreter Tool for weather analysis
- Memory for storing user preferences
- S3 bucket for results storage

**Features:** Scrapes weather.gov, analyzes conditions, stores preferences, generates recommendations  
**Deployment time:** ~15-20 minutes  
**Estimated cost:** ~$100-150/month

**Implementation:** [CloudFormation](./cloudformation/end-to-end-weather-agent/) | [CDK](./cdk/python/end-to-end-weather-agent/) | [Terraform](./terraform/end-to-end-weather-agent/)

## Prerequisites

Before deploying any sample, ensure you have:

1. **AWS Account** with appropriate permissions
2. **AWS CLI** installed and configured
3. **Access to Amazon Bedrock AgentCore** (preview)
4. **IAM Permissions** to create:
   - CloudFormation stacks (for CloudFormation samples)
   - IAM roles and policies
   - ECR repositories
   - Lambda functions
   - AgentCore resources
   - S3 buckets (for weather agent)

For CDK samples, also install:
- Python 3.8+
- AWS CDK v2.218.0 or later

For Terraform samples, also install:
- Terraform >= 1.6 (recommend [tfenv](https://github.com/tfutils/tfenv) for version management)
- Note: `brew install terraform` provides v1.5.7 which is deprecated

## Repository Structure

```
04-infrastructure-as-code/
├── README.md                          # This file
├── cloudformation/                    # CloudFormation samples
│   ├── README.md                      # CloudFormation-specific guide
│   ├── basic-runtime/
│   ├── mcp-server-agentcore-runtime/
│   ├── multi-agent-runtime/
│   └── end-to-end-weather-agent/
├── cdk/                              # CDK samples (Python & TypeScript)
│   ├── README.md                     # CDK-specific guide
│   ├── python/                       # Python CDK samples
│   │   ├── basic-runtime/
│   │   ├── mcp-server-agentcore-runtime/
│   │   ├── multi-agent-runtime/
│   │   └── end-to-end-weather-agent/
│   └── typescript/                   # TypeScript CDK samples
│       └── knowledge-base-rag-agent/
└── terraform/                        # Terraform samples
    ├── README.md                     # Terraform-specific guide
    ├── basic-runtime/
    ├── mcp-server-agentcore-runtime/
    ├── multi-agent-runtime/
    └── end-to-end-weather-agent/
```

## Additional Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
- [AWS CloudFormation Documentation](https://docs.aws.amazon.com/cloudformation/)
- [AWS CDK Documentation](https://docs.aws.amazon.com/cdk/)
- [Terraform Documentation](https://www.terraform.io/docs)
- [Workshops](../06-workshops/) - Learn AgentCore fundamentals in Jupyter Notebooks
