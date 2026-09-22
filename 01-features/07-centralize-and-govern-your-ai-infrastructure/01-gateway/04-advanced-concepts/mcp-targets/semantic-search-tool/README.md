# Amazon Bedrock AgentCore gateway - Semantic search tutorial

## Tutorial Details

| Information         | Details                                                               |
| :------------------ | :-------------------------------------------------------------------- |
| Tutorial type       | Conversational                                                        |
| Agent type          | Single                                                                |
| AgentCore services  | AgentCore gateway, AgentCore identity                                 |
| Agentic Framework   | Strands Agents                                                        |
| LLM model           | Anthropic Claude Haiku 4.5                                            |
| Tutorial components | Creating and using Lambda-backed AgentCore gateway from Strands Agent |
| Tutorial vertical   | Cross-vertical                                                        |
| Example complexity  | Easy                                                                  |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                          |

### Tutorial Architecture

Amazon Bedrock AgentCore gateway provides unified connectivity between agents and the tools and resources they need to interact with. gateway plays multiple roles in this connectivity layer:

1. **Security Guard**: gateway manages OAuth authorization to ensure only valid users / agents access tools / resources.
2. **Translator**: gateway translates agent requests made using popular protocols like the Model Context Protocol (MCP) into API requests and Lambda invocations. This means developers don't need to host servers, manage protocol integration, version support, version patching, etc.
3. **Composer**: gateway enables developers to seamlessly combine multiple APIs, functions, and tools into a single MCP endpoint that an agent can use.
4. **Keychain**: gateway handles the injection of the right credentials to use with the right tool, ensuring that agents can seamlessly leverage tools that require different sets of credentials.
5. **Researcher**: gateway enables agents to search across all of their tools to find only the ones that are best for a given context or question. This allows agents to make use of 1000s of tools instead of just a handful. It also minimizes the set of tools that need to be provided in an agent's LLM prompt, reducing latency and cost.
6. **Infrastructure Manager**: gateway is completely serverless, and comes with built-in observability and auditing, alleviating the need for developers to manage additional infrastructure to integrate their agents and tools.

![How does it work](images/gw-arch-overview.png)

### Tutorial Key Features

- Creating Amazon Bedrock AgentCore Gateways with AWS Lambda-backed targets
- Using AgentCore gateway semantic search
- Using Strands Agents to show how AgentCore gateway search improves latency

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for Claude Sonnet 4

## AgentCore gateway helps solve the challenge of MCP servers that have large numbers of tools

In a typical enterprise setting, agent builders encounter MCP servers that have hundreds or even thousands
of MCP tools. This volume of tools poses challenges for AI agents, including poor tool selection accuracy,
increased cost, and higher latency driven by higher token usage from excessive tool metadata.
This can happen when connecting your agents to third party services (e.g., Zendesk, Salesforce,
Slack, JIRA, ...), or to existing enterprise REST services.
AgentCore gateway provides a built in semantic search across tools,
which improves agent latency, cost, and accuracy, while still giving those agents the tools they need.
Depending on your use case, LLM model, and agent framework, you can see up to 3x better latency by keeping
an agent focused on relevant tools versus providing the full set of hundreds of tools from a typical MCP Server.

![How does it work](images/gateway_tool_search.png)

## What you will learn

By the end of this step-by-step tutorial, you will understand:

- How to use AgentCore gateway's built-in search tool to quickly find relevant tools
- How to integrate tool search results into Strands Agents for improved latency and reduced cost

## Understanding fundamentals of AgentCore gateway Search

When you create an AgentCore gateway, you have the option to indicate that you want Search enabled.
For Gateways with search enabled, three things happen:

1. **Vector store is created**. The gateway service automatically creates a serverless fully-managed vector store for your new gateway. This enables a full semantic search across your gateway tools.
2. **Vector store is populated**. As you add gateway Targets to your gateway, the service automatically uses embeddings behind the scenes to populate the vector store based on the tools from the new Target. The tool metadata comes from the JSON defintions of your tools or the OpenAPI Schema specification for your REST services targets.
3. **Search tool (MCP based) is provided**. In addition to all of your user defined tools (from AWS Lambda targets or REST services), the gateway gets one additional MCP tool that provides semantic search. It is named `x-amz-bedrock-agentcore-search`. The prefix ensures there are no name clashes with your user-defined tools. We may add more tools like that in the future as well. The search tool has a single argument called `query`. When the search tool is invoked, the gateway service performs a semantic search using that query, matching it against available tool metadata (names, descriptions, input and output schema), and returns the most relevant tools in descending order of relevance.

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/).

Once deployed, export the stack name:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
```

### Step 2: Deploy Lambda Functions (CloudFormation)

Deploy the calculator and restaurant Lambda functions:

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
export LAMBDA_STACK_NAME="agentcore-semantic-search-lambdas"
export CFN_BUCKET="agentcore-cfn-$(aws sts get-caller-identity --query Account --output text)-$(aws configure get region)"

aws s3 mb "s3://$CFN_BUCKET" 2>/dev/null || true

aws cloudformation package \
  --template-file cloudformation/semantic-search/semantic-search-stack.yaml \
  --s3-bucket $CFN_BUCKET \
  --output-template-file /tmp/semantic-search-packaged.yaml

aws cloudformation deploy \
  --template-file /tmp/semantic-search-packaged.yaml \
  --stack-name $LAMBDA_STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

Capture the Lambda ARNs:

```bash
export CALC_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`CalcFunctionArn`].OutputValue' --output text)

export RESTAURANT_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`RestaurantFunctionArn`].OutputValue' --output text)

echo "Calc ARN:       $CALC_ARN"
echo "Restaurant ARN: $RESTAURANT_ARN"
```

### Step 3: Deploy gateway + Targets

The deploy script creates the Cognito resources, gateway with semantic search enabled, and five Lambda targets (FoodTools, CalcTools, Calc2, Calc3, Calc4) exposing 300+ tools:

```bash
uv sync
unset GATEWAY_ID GATEWAY_URL
uv run python scripts/semantic-search/deploy.py
```

The script saves resource identifiers to `scripts/semantic-search/.env` for use by invoke and cleanup.

Capture the gateway URL from the deploy output:

```bash
source scripts/semantic-search/.env
export GATEWAY_URL

echo "gateway URL: $GATEWAY_URL"
```

### What the deployment creates

At a high level, the steps for setting up your gateway are:

1. Define what identity providers and credential providers you are using for inbound (agents calling Gateways) and outbound (Gateways calling tools) security.
2. Create the gateway using `create_gateway` with `searchType: SEMANTIC` in the protocol configuration.
3. Add gateway Targets using `create_gateway_target`, to expose MCP tools that will be implemented in AWS Lambda.

In this tutorial, we use Amazon Cognito as the identity provider (IdP), AWS Lambda functions as targets, and AWS IAM for outbound authentication. The same concepts demonstrated in this tutorial still apply when using other IdP's or other target types.

The calculator target includes 4 basic tools (add, subtract, multiply, divide) plus 75 generated tool definitions for investment management (trading, credit research, quantitative analysis, portfolio management). By adding multiple copies of this target, the gateway exposes 300+ tools to demonstrate the power of semantic search.

![How does it work](images/gateway_secure_access.png)

### Using MCP Inspector against your gateway

Once deployed, you can explore the MCP server with the MCP Inspector tool. From your terminal window, enter `npx @modelcontextprotocol/inspector` to launch the MCP Inspector. Then paste in your gateway endpoint URL and your JWT bearer token to connect. Once connected, try List Tools and Invoke Tool.

![MCP Inspector](images/mcp_inspector.png)

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../05-community/gateway-mcp-inspector/) to explore semantic search interactively.

![demo](./images/demo.gif)

### Option 1: Run the invoke script

The invoke script demonstrates semantic search by listing all 300+ tools, then using `x_amz_bedrock_agentcore_search` to find relevant subsets in under one second:

```bash
uv run python scripts/semantic-search/invoke.py
```

Expected output shows:
- Full tool list (300+ tools across all targets)
- Search for "credit research tools" returning the most relevant matches
- Search for "restaurant reservation tools" returning food-related tools
- Search for "multiplying two numbers" returning math tools

### Option 2: Strands Agent with search

This example shows how to combine semantic search with a Strands Agent for improved latency. Run the script:

```bash
uv run python scripts/semantic-search/strands_demo.py
```

The script performs three steps:
1. Uses `x_amz_bedrock_agentcore_search` to find tools matching "adding numbers"
2. Creates a Strands Agent with only the relevant tools (not all 300+)
3. Runs the agent with the query "add 100 plus 50"

Source: [`scripts/semantic-search/strands_demo.py`](../../gatewaylabproject/scripts/semantic-search/strands_demo.py)

<details>
<summary>Code snippet (click to expand)</summary>

```python
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient, MCPAgentTool
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool as MCPTool
import json
import requests

# Connect to the gateway
gateway_url = "<your-gateway-url>"
jwt_token = "<your-jwt-token>"

# Use the search tool to find relevant tools
requestBody = {
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {
        "name": "x_amz_bedrock_agentcore_search",
        "arguments": {"query": "tools for adding numbers"}
    }
}
response = requests.post(
    gateway_url, json=requestBody,
    headers={"Authorization": f"Bearer {jwt_token}", "Content-Type": "application/json"}
)
tools_found = response.json()["result"]["structuredContent"]["tools"]

# Create agent with ONLY the relevant tools (not all 300+)
client = MCPClient(
    lambda: streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {jwt_token}"}
    )
)
model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0", temperature=0.7)

with client:
    strands_tools = []
    for tool in tools_found[:3]:
        mcp_tool = MCPTool(name=tool["name"], description=tool["description"], inputSchema=tool["inputSchema"])
        strands_tools.append(MCPAgentTool(mcp_tool, client))

    agent = Agent(model=model, tools=strands_tools)
    result = agent("add 100 plus 50")
    print(result.message['content'][0]['text'])
```

</details>

By using search to narrow 300+ tools to just the relevant subset, you can see up to 3x better latency compared to passing all tools to the agent.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory, remove all resources created by deploy:

```bash
uv run python scripts/semantic-search/cleanup.py
```

This deletes the gateway (including all targets) and its IAM role.

Delete the Lambda CloudFormation stack and S3 bucket:

```bash
aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME
aws s3 rb "s3://$CFN_BUCKET" --force
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Conclusion

In this tutorial, you have learned about Amazon Bedrock AgentCore gateway and its built-in
fully managed semantic search capability. You have seen the following:

- how to create a gateway with semantic search enabled
- how to add multiple gateway targets to surface 300+ MCP tools from a single endpoint
- how to list the tools on your gateway using 3 different approaches
- how to use the built-in semantic search tool to find relevant tools
- how to integrate search with your Strands Agent
- how to compare performance of an agent using a server with hundreds of tools versus one that uses semantic search to narrow tools to a specific topic

AgentCore gateway search is helpful for more advanced use cases as well. By offering the search as a native
MCP tool and not just a control plane API, you can imagine giving your agents more autonomy to discover new
MCP servers, and find new capabilities at runtime leading to breakthroughs in solving more challenging problems.
In addition, search is an important foundation for MCP registries and supporting agent developers as they
design and build new agents.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Lambda as Target](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-lambda.html)
- [identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
