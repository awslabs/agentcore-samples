# OpenAPI APIs as MCP Tools with API Key Authorization

## Overview

Bedrock AgentCore gateway can turn OpenAPI specifications (JSON or YAML) into MCP-compatible tools without requiring you to manage infrastructure or hosting. Each operation defined in the OpenAPI file becomes an MCP tool, accessible through a single gateway endpoint URL.

This tutorial builds a Mars Weather agent that calls NASA's Open APIs using API key authorization. The agent uses Strands Agents with Amazon Bedrock models to query weather data from NASA's InSight mission.

![architecture](./images/openapi-gateway-apikey.png)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A NASA API key (free, register at [api.nasa.gov](https://api.nasa.gov/) -- the key arrives by email within minutes)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Create AgentCore gateway (AgentCore CLI)

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
agentcore add gateway \
  --name openapi-apikey-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'openapi-apikey-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 3: Register NASA API Key and Create OpenAPI Target (boto3)

Set your NASA API key and gateway ID as environment variables. A NASA API key (free, register at [api.nasa.gov](https://api.nasa.gov/)- the key arrives by email within minutes).

```bash
export NASA_API_KEY="<your-nasa-api-key>"
export GATEWAY_ID="$GATEWAY_ID"
```

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/openapi-apikey/deploy_target.py
```

This script:

1. Creates an API key credential provider for the NASA API key
2. Uploads the NASA OpenAPI spec to S3
3. Creates the OpenAPI gateway target with API key outbound auth configured to pass `api_key` as a query parameter

### Step 4: Verify Deployment

```bash
aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier $GATEWAY_ID \
  --query 'items[].{name:name,status:status}' --output table
```

Ensure all targets are in `READY` state.

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../../05-community/gateway-mcp-inspector/) to explore NASA tools interactively.

![demo](./images/demo.gif)

### Option 1: AgentCore gateway MCP Client

```bash
uv sync
uv run python scripts/openapi-apikey/invoke.py
```

This lists all NASA API tools exposed through the gateway and calls `getInsightWeather`.

### Option 2: Strands Agents (optional)

```bash
uv run python scripts/openapi-apikey/strands_demo.py
```

The script connects a Strands Agent to the gateway, fetches a Cognito token automatically, and queries Mars weather:

```python
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

client = MCPClient(
    lambda: streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {token}"}
    )
)

model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

with client:
    tools = client.list_tools_sync()
    agent = Agent(model=model, tools=tools)
    agent("What is the weather in the northern part of Mars?")
```

### Option 3: MCP SDK (optional)

```bash
uv run python scripts/openapi-apikey/mcp_invoke.py
```

The script uses the MCP Python SDK directly with streamable HTTP transport:

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async with streamablehttp_client(
    gateway_url, headers={"Authorization": f"Bearer {token}"}
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print(tools)
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory.

Delete the target, API key credential provider, and S3 bucket (created via boto3 in Step 3):

```bash
uv run python scripts/openapi-apikey/cleanup.py
```

Remove the gateway target and gateway:

```bash
agentcore remove gateway --name openapi-apikey-gateway -y
agentcore deploy --yes
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.



Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [OpenAPI Schema Target](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-openapi.html)
- [identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
