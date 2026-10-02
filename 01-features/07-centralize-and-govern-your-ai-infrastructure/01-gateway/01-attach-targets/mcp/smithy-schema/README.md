# Smithy APIs as MCP Tools with Bedrock AgentCore gateway

## Overview

Bedrock AgentCore gateway provides customers a way to turn their existing Smithy APIs into fully-managed MCP servers without needing to manage infra or hosting. Customers can bring Smithy spec and transform them into mcp tools. We will demonstrate creating mcp tools from smithy model of Amazon S3. The agent will then be able to query Amazon S3 and answer questions related to the it.

![architecture](./images/architecture.png)

The gateway workflow involves the following steps to connect your agents to external tools:

- **Create the tools for your gateway**: Define your tools using Smithy specification. -**Create a gateway endpoint**: Create the gateway that will serve as the MCP entry point with inbound authentication. 
- **Add targets to your gateway**: Configure the Smithy target that define how the gateway routes requests to specific tools. All the operations that part of Smithy file will become an MCP-compatible tool, and will be made available through your gateway endpoint URL. Configure outbound authorization using AWS IAM for invoking Amazon S3 apis via Smithy. 
- **Update your agent code**: Connect your agent to the gateway endpoint to access all configured tools through the unified MCP interface.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `us.amazon.nova-pro-v1:0` (if using Strands demo)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory. Navigate there before proceeding.

All tutorials share a single AgentCore CLI project at [`gatewaylabproject/`](../../../../gatewaylabproject/). Navigate to that directory and run all subsequent CLI commands from there.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../00-optional-setup/).

Once the stack is deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

echo "Discovery URL:          $DISCOVERY_URL"
echo "gateway Client ID:      $GATEWAY_CLIENT_ID"
echo "gateway Client Secret:  $GATEWAY_CLIENT_SECRET"
echo "Token Endpoint:         $TOKEN_ENDPOINT"
```

### Step 2: Create AgentCore gateway (AgentCore CLI)

Create the gateway with inbound JWT auth. The `--client-id` and `--client-secret` flags allow the CLI to fetch gateway bearer tokens for testing.

```bash
agentcore add gateway \
  --name agentcore-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG
```

### Step 3: Create Smithy gateway Target (AgentCore CLI)

Create the gateway target pointing to the Amazon S3 Smithy spec. The Smithy spec is at [`smithy-specs/s3-apis.json`](../../../gatewaylabproject/smithy-specs/s3-apis.json). Outbound authorization uses IAM, so the gateway's IAM role must have permissions to invoke Amazon S3 APIs.

```bash
agentcore add gateway-target \
  --name smithy-s3-iam-target \
  --type smithy-model \
  --schema smithy-specs/s3-apis.json \
  --gateway agentcore-gateway
```

### Step 4: Deploy gateway and Target (AgentCore CLI)

```bash
agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'agentcore-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 5: Grant S3 Permissions to gateway Role

The gateway's IAM role needs S3 permissions to invoke the Smithy APIs on your behalf:

```bash
uv run python scripts/smithy-iam/grant_s3_permissions.py
```

Verify everything is running:

```bash
agentcore status
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore Smithy tools interactively.

![demo](./images/demo.gif)

### Option 1: AgentCore gateway MCP Client

```bash
uv sync
uv run python scripts/smithy-iam/invoke.py
```

This lists all S3 tools exposed through the gateway and calls `ListBuckets`.

### Option 2: Strands Agents (optional)

```bash
uv run python scripts/smithy-iam/strands_demo.py
```

The script connects a Strands Agent to the gateway, fetches a Cognito token automatically, and invokes S3 tools via natural language:

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
    agent("List all the S3 buckets in my account")
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, remove the S3 IAM policy and then CLI resources:

```bash
uv run python scripts/smithy-iam/cleanup.py
```

```bash
agentcore remove gateway-target --name smithy-s3-iam-target -y
agentcore remove gateway --name agentcore-gateway -y
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
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
- [identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
