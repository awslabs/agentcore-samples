# Web Search Tool

Give your agents live web access through a fully managed, MCP-compliant connector. No search APIs to provision, no outbound credentials to manage, and no result-parsing glue to maintain.

## Overview

AI agents have a fundamental limitation: their knowledge is frozen at training time. When a user asks about today's stock prices, breaking news, or a release that shipped an hour ago, an agent relying only on its training data cannot answer accurately.

Building a custom web search integration to fix this is a project in itself: procuring third-party search APIs, managing keys and quotas, parsing inconsistent result formats, extracting relevant snippets, reasoning about where queries travel, and maintaining coverage over time.

The Web Search Tool on Amazon Bedrock AgentCore removes that complexity. It is a built-in connector you attach to your gateway with `connectorId: "web-search"`. Agents discover it with a standard `tools/list` call and invoke it like any other MCP tool.

![arch](../images/architecture.png)

## Key capabilities

- **Near real-time information access**: current results with titles, URLs, snippets, and publication dates to ground responses.
- **Zero infrastructure management**: no third-party search APIs to provision or scale. The gateway exposes web search as a standard MCP tool.
- **Framework agnostic**: works with Strands, LangChain, LangGraph, CrewAI, or any MCP-compatible client.
- **Purpose-built web index**: backed by an Amazon-owned index spanning tens of billions of documents, not a thin wrapper over a third-party engine.
- **Knowledge graph for high-confidence facts**: grounds entities and relationships rather than leaving the model to infer them from page text.
- **Semantic snippet extraction**: returns the passages that bear on the query, optimized for the model's context window, instead of raw HTML.

## Private by design

- **Queries never leave AWS**: searches are served within AWS infrastructure. The gateway authenticates to the AWS-owned connector and routes the request internally, so the data path stays inside AWS end to end.
- **Your data is never used for training**: your queries and the data they touch are not used to train models or improve the service.

## How it works

1. **Gateway setup**: create a gateway and add a Web Search target with `connectorId: "web-search"`. The gateway snapshots the tool schema and provisions the integration.
2. **Tool discovery**: your agent calls `tools/list` and discovers the WebSearch tool with its input schema.
3. **Search invocation**: your agent calls `tools/call` with a natural language query. The gateway authenticates to the backend and routes the request internally within AWS.
4. **Structured results**: the tool returns results with semantically relevant snippets, URLs, titles, and publication dates.
5. **Grounded response**: your agent composes a response with cited sources.

## Tutorial Details

| Information          | Details                               |
| :------------------- | :------------------------------------ |
| Tutorial type        | Interactive                           |
| AgentCore components | AgentCore gateway, AgentCore identity |
| gateway Target type  | MCP (built-in connector)              |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)           |
| Outbound Auth        | AWS IAM (GATEWAY_IAM_ROLE)            |
| Example complexity   | Easy                                  |
| SDK used             | boto3 + MCP client, Strands           |

> [!NOTE]
> The Web Search Tool connector is available in the US East (N. Virginia) `us-east-1` Region. Run this tutorial with your AWS profile set to `us-east-1`.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`), Region `us-east-1`
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Amazon Bedrock model access for the Strands demo model

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on the connector. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../00-optional-setup/). Once deployed, capture the stack name:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
```

### Step 2: Create the gateway (boto3)

Create the gateway with the Web Search permissions. The `--websearch-targets` flag grants the gateway role `bedrock-agentcore:InvokeGateway` and `bedrock-agentcore:InvokeWebSearch`:

```bash
uv run python scripts/deploy_gateway.py \
  --name websearch-gateway \
  --websearch-targets \
  --env-file scripts/websearch/.env
```

### Step 3: Attach the Web Search Tool target

```bash
uv run python scripts/websearch/deploy.py
```

This creates the target with the boto3 `create_gateway_target` API:

```python
import boto3

gateway_client = boto3.client("bedrock-agentcore-control", region_name="us-east-1")

gateway_client.create_gateway_target(
    name="web-search-tool",
    gatewayIdentifier="<GATEWAY_ID>",
    targetConfiguration={
        "mcp": {"connector": {"source": {"connectorId": "web-search"}}}
    },
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
)
```

### Step 4 (optional): Configure domain filtering

You can restrict which domains the tool may query with a denylist. This is optional; run it only when you want domain governance. It updates the target's WebSearch configuration:

```bash
uv run python scripts/websearch/set_domain_filter.py blocked-website-1.com blocked-website-2.com
```

The corresponding configuration on the target is:

```python
"configurations": [
    {
        "name": "WebSearch",
        "parameterValues": {
            "domainFilter": {"exclude": ["blocked-website-1.com", "blocked-website-2.com"]}
        },
    }
]
```

### Per-tool configuration: parameterValues and parameterOverrides

Each entry in a connector target's `configurations` list shapes one tool (matched by `name`, here `WebSearch`). Two fields control how the tool behaves for your agents:

- **`parameterValues`**: fixed or default values the gateway sets on the agent's behalf. The agent does not supply these; the gateway applies them to every call. The domain denylist above is a `parameterValues` entry: every search runs with `domainFilter.exclude` applied, whether or not the agent asks for it. Use it to pin governance settings or sensible defaults.
- **`parameterOverrides`**: adjust how individual parameters are exposed to the agent at runtime. Each override targets a parameter by JSON Pointer `path` and can set an agent-facing `description` and `visible` (whether the agent can see and set it). Use it to rename a parameter for clarity, or hide a parameter you do not want the agent to control.

```python
"configurations": [
    {
        "name": "WebSearch",
        # Gateway-set values the agent never supplies
        "parameterValues": {
            "domainFilter": {"exclude": ["blocked-website-1.com"]}
        },
        # Tune what the agent sees and can set at runtime
        "parameterOverrides": [
            {
                "path": "/maxResults",
                "description": "How many results to return (1 to 25)",
                "visible": True,
            }
        ],
    }
]
```

In short: `parameterValues` is what the gateway fixes for the agent; `parameterOverrides` is what the agent can see and set. Together they let you take a connector tool and govern it for your use case.

## Demo

Install Python dependencies (first time only):

```bash
uv sync
```

### Option 1: AgentCore Gateway MCP Inspector

Connect the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to your gateway, select the WebSearch tool, and run a query interactively.

![demo](./images/demo.gif)

### Option 2: MCP client

[`invoke.py`](../../../../gatewaylabproject/scripts/websearch/invoke.py) lists the gateway tools, finds the WebSearch tool, calls it, and prints the structured results. The tool name is prefixed by the target name (`{target}___WebSearch`):

```python
from gateway_mcp_client import GatewayMCPClient

mcp = GatewayMCPClient(gateway_url, token_fn, protocol_version="2025-11-25")

# Discover the tool (names are prefixed with the target name)
tools = mcp.list_all_tools()
websearch_tool = next(t["name"] for t in tools if t["name"].lower().endswith("websearch"))

# Call it; the first text block holds a JSON payload with the results
result = mcp.call_tool(websearch_tool, {"query": "what shipped in python 3.13", "maxResults": 5})
for block in result.get("content", []):
    if block.get("type") == "text":
        for r in json.loads(block["text"]).get("results", []):
            print(r["title"], r["url"], r.get("publishedDate"))
            print(r["text"][:200])
```

```bash
uv run python scripts/websearch/invoke.py "what shipped in python 3.13"
```

### Option 3: Strands agent

[`strands_demo.py`](../../../../gatewaylabproject/scripts/websearch/strands_demo.py) gives a Strands agent the WebSearch tool and asks a current-events question. The agent searches the live web and cites sources:

```python
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient

# Point the MCP client at the gateway; the gateway JWT is the bearer token
client = MCPClient(
    lambda: streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {token}"}
    )
)
model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

with client:
    tools = client.list_tools_sync()  # includes the WebSearch tool
    agent = Agent(
        model=model,
        tools=tools,
        system_prompt=(
            "You answer using the WebSearch tool for anything time-sensitive. "
            "Cite the source URL for each fact you report."
        ),
    )
    agent("What are the latest AWS announcements?")
```

```bash
uv run python scripts/websearch/strands_demo.py "What are the latest AWS announcements?"
```

## Input schema

The WebSearch tool accepts the following input when invoked via `tools/call`:

| Field      | Type    | Required | Description                                                           |
| :--------- | :------ | :------- | :-------------------------------------------------------------------- |
| query      | string  | Yes      | The search query string. Must be 200 characters or fewer.             |
| maxResults | integer | No       | Maximum number of results to return. Valid range 1 to 25, default 10. |

## Response format

The tool returns results in MCP-compliant format. The text content holds a JSON payload whose `results` entries contain:

| Field         | Type   | Required | Description                           |
| :------------ | :----- | :------- | :------------------------------------ |
| text          | string | Yes      | Text content or snippet of the result |
| url           | string | No       | URL of the source webpage             |
| title         | string | No       | Title of the source webpage           |
| publishedDate | string | No       | Publication date of the result        |

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, run the cleanup script. It deletes the Web Search target, the gateway, the gateway IAM role, and the tutorial's `.env` file:

```bash
uv run python scripts/websearch/cleanup.py
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Terms and conditions

If you use Web Search Tool, you are responsible for your use, and any use by your end users, of content retrieved from Web Search Tool (Search Results). You must retain and display the source citations and links provided with each Search Result in any output you surface to end users that uses the Search Result. You may not use Web Search Tool to (a) extract, store, or reproduce content from Search Results in bulk, or (b) build or populate a competing index or database.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
