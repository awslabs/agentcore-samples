# AgentCore gateway — Elicitation + Sampling

## Overview

Elicitation lets an MCP server pause execution mid tool-call and request input from the client (form-mode, URL-mode) or surface a `URLElicitationRequiredError` that the client must satisfy before retrying. Sampling lets a server delegate text generation to the client's LLM via `sampling/createMessage`. Both require **streaming and sessions enabled** on the gateway, since:

- Elicitation requires the server-to-client SSE channel to deliver the `elicitation/create` request, and a session to correlate the response.
- Sampling uses the same SSE channel for `sampling/createMessage`.

```bash
{
  "protocolConfiguration": {
    "mcp": {
      "sessionConfiguration": {
        "sessionTimeoutInSeconds": 3600
      },
      "streamingConfiguration": {
        "enableResponseStreaming": true
      }
    }
  }
}
```

This notebook stands up a gateway with **both** `streamingConfiguration.enableResponseStreaming` and `sessionConfiguration` enabled (no Lambda interceptor) and walks through every elicitation/sampling pattern end-to-end against a dedicated MCP server (`labelicitation`).

![Elicitation diagram placeholder](../images/elicitation.png)

## Workshop roadmap

| Step   | What you do                                                                            |
| ------ | -------------------------------------------------------------------------------------- |
| **1**  | Set up the notebook.                                                                   |
| **2**  | Create the gateway with both streaming + sessions enabled.                             |
| **3**  | Deploy the `labelicitation` FastMCP server to AgentCore runtime.                       |
| **4**  | Wire it in as a gateway target.                                                        |
| **5**  | Form-mode elicitation — `book_room` (single object schema).                            |
| **6**  | Boolean confirmation — `cancel_with_confirm`.                                          |
| **7**  | Sequential elicitations — `log_expense` (3 prompts in one tool call).                  |
| **8**  | Sampling — `sampling_demo` (server asks client's LLM).                                 |
| **9**  | Long-compute + elicitation — `optimize_and_apply`.                                     |
| **10** | URL-mode elicitation Flow §4.2 — `connect_external_account` + completion notification. |
| **11** | Clean up.                                                                              |

## Tutorial Details

| Information          | Details                                                                                                                                  |
| :------------------- | :--------------------------------------------------------------------------------------------------------------------------------------- |
| Tutorial type        | Interactive                                                                                                                              |
| AgentCore components | AgentCore gateway, AgentCore identity, AgentCore runtime                                                                                 |
| gateway target type  | MCP server                                                                                                                               |
| gateway features     | Streaming ON, Sessions ON, no interceptor                                                                                                |
| MCP transport        | Streamable HTTP, SSE bidirectional                                                                                                       |
| Inbound auth         | Cognito (M2M)                                                                                                                            |
| Outbound auth        | Cognito (M2M) via OAuth2 credential provider                                                                                             |
| SDK used             | boto3 + `mcp.client.session.ClientSession` for form-mode + sampling, raw httpx for URL-mode (which `mcp` 1.27.0 doesn't auto-handle yet) |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `global.anthropic.claude-haiku-4-5-20251001-v1:0` (required for sampling demo)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Register MCP Server (AgentCore CLI)

The MCP server code is at [`gatewaylabproject/app/labelicitation/main.py`](../../../../gatewaylabproject/app/labelicitation/main.py). It exposes elicitation tools (`book_room`, `cancel_with_confirm`, `log_expense`, `connect_external_account`, `optimize_and_apply`) and a sampling tool (`sampling_demo`).

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
agentcore add agent \
  --name elicitation_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/labelicitation \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
```

### Step 3: Create AgentCore gateway with Streaming + Sessions (boto3)

> [!NOTE]
> Elicitation and sampling require both streaming and sessions enabled. The AgentCore CLI does not yet support these options, so this step uses a boto3 script.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/deploy_gateway.py \
  --name elicitation-gateway \
  --streaming \
  --sessions \
  --env-file scripts/elicitation/.env
```

Export the gateway ID and URL (saved by the deploy script):

```bash
source scripts/elicitation/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 4: Deploy MCP Server and gateway (AgentCore CLI)

```bash
agentcore deploy --yes
```

Capture the MCP server URL:

```bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'elicitation_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 5: Create gateway Target (boto3)

```bash
uv run python scripts/deploy_target.py \
  --name elicitation-mcp-server-target \
  --gateway-env-file scripts/elicitation/.env
```

## Demo

> [!TIP]
> Elicitation and sampling are interactive, bidirectional flows that require a client capable of handling SSE mid-stream requests. Use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore these features interactively.

![demo](./images/demo.gif)

### Step 5: Form-mode elicitation — `book_room`

Server prompts for `room_type`, `nights`, `breakfast` via a Pydantic schema. Fill in the form in the Inspector and submit. Server returns the booking confirmation string.

### Step 6: Boolean confirmation — `cancel_with_confirm`

Single-field boolean elicitation before a destructive action. The Inspector shows a confirm/deny dialog.

### Step 7: Sequential elicitations — `log_expense`

Three `elicitation/create` requests in sequence within one tool call: category → description → confirm. The Inspector prompts for each in turn.

### Step 8: Sampling — `sampling_demo`

Server calls `ctx.sample(prompt)`. The Inspector forwards the request to its configured LLM and returns the model's reply. Tool returns whatever the LLM said.

### Step 9: Long-compute + elicitation gate — `optimize_and_apply`

Spins for `duration_seconds`, emitting a progress notification every `interval_seconds`, then prompts the user to approve a destructive action via form elicitation. The Inspector shows progress updates followed by the approval form.

### Step 10: URL-mode elicitation Flow §4.2 — `connect_external_account`

Per MCP spec 2025-11-25, URL-mode elicitation lets the server send a URL the client should present to the user (OAuth consent screen, payment flow, etc.) instead of a JSON form. The Inspector opens the URL and waits for the completion notification.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
uv run python scripts/cleanup_gateway.py \
  --name elicitation-gateway \
  --env-file scripts/elicitation/.env
```

Remove the CLI-managed agent and credentials:

```bash
agentcore remove agent --name elicitation_mcp_server -y
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [MCP Elicitation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-elicitation.html)
- [MCP Sampling](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-sampling.html)
