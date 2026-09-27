# Strands Agent with AWS API MCP Server Integration

| Information         | Details                                                                      |
|---------------------|------------------------------------------------------------------------------|
| Agent type          | Synchronous                                                                 |
| Agentic Framework   | Strands                                                                    |
| LLM model           | Anthropic Claude 4 (Or anything supported by Bedrock)                     |
| Components          | AgentCore Runtime, AWS API MCP Server                                        |
| Example complexity  | Intermediate                                                                 |
| SDK used            | Amazon Bedrock AgentCore Python SDK                                           |

This example demonstrates how to integrate a Strands agent with the AWS API MCP Server, enabling your agent to interact with AWS services through natural language. The MCP server runs as a subprocess.

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- AWS account with Bedrock AgentCore access
- AWS credentials configured
- IAM permissions for AWS services you want the agent to access

## Setup Instructions

### 1. Create a Python Environment with uv

```bash
# Create and activate a virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Requirements

```bash
uv pip install -r requirements.txt
```

### 3. Understanding the Agent Code

The `strands_agent_with_aws_mcp.py` file contains a Strands agent that dynamically loads AWS tools from the MCP server subprocess:

```python
from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import stdio_client, StdioServerParameters
from strands.tools.mcp import MCPClient

# Initialize BedrockAgentCore app
app = BedrockAgentCoreApp()

# Configure MCP client to launch AWS API server as subprocess
stdio_mcp_client = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(
            command="uvx",
            args=["awslabs.aws-api-mcp-server@latest"],
        )
    )
)

@app.entrypoint
def agent_invocation(payload, context):
    """Handler for agent invocation with AWS capabilities"""
    with stdio_mcp_client:
        # Get AWS tools from the MCP server
        tools = stdio_mcp_client.list_tools_sync()

        # Create an agent with these tools
        agent = Agent(tools=tools)

        user_message = payload.get("prompt", "")
        if not user_message:
            return {"error": "No prompt provided. Please include a 'prompt' field."}

        result = agent(user_message)
        return {"result": result.message}

app.run()
```

### 4. Configure and Launch with Bedrock AgentCore Toolkit

```bash
# Configure your agent for deployment
agentcore configure -e strands_agent_with_aws_mcp.py

# Deploy your agent
agentcore launch
```

### 5. Testing Your Agent

Once deployed, you can test your agent with AWS operations:

```bash
# List S3 buckets
agentcore invoke '{"prompt": "List all my S3 buckets"}'

# Check EC2 instances
agentcore invoke '{"prompt": "Show me all running EC2 instances"}'

# Create an S3 bucket
agentcore invoke '{"prompt": "Create a new S3 bucket called my-test-bucket-2025"}'

# List Lambda functions
agentcore invoke '{"prompt": "List all Lambda functions"}'
```

## Architecture

The AWS API MCP Server runs as a subprocess within the agent deployment:

```
┌──────────────────────────────────┐
│  AgentCore Deployment            │
│  ┌────────────────────────────┐  │
│  │  Strands Agent             │  │
│  │  (Claude 3.5 Sonnet)       │  │
│  └─────────┬──────────────────┘  │
│            │ stdio               │
│            ↓                     │
│  ┌────────────────────────────┐  │
│  │  AWS API MCP Server        │  │
│  │  (subprocess)              │  │
│  └─────────┬──────────────────┘  │
└────────────┼─────────────────────┘
             ↓
      AWS Services
```

## IAM Permissions

Ensure the agent's execution role has permissions for AWS services you want to access. Example policy:

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListAllMyBuckets",
        "ec2:DescribeInstances",
        "lambda:ListFunctions"
      ],
      "Resource": "*"
    }
  ]
}
```

## Additional Resources

- [AWS API MCP Server](https://github.com/awslabs/mcp/tree/main/src/aws-api-mcp-server)
- [Strands Documentation](https://github.com/anthropics/anthropic-sdk-python)
- [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
