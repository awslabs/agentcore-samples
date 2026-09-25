# AgentCore gateway — Streamable HTTP

## Overview

MCP response streaming enables your AgentCore gateway to deliver real-time Server-Sent Events (SSE) to clients during tool execution. Instead of waiting for the entire tool call to complete before returning a response, the gateway streams events as they occur — including [progress notifications](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-progress.html), [log messages](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-logging.html), [elicitation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-elicitation.html) requests, and [sampling](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-sampling.html) requests.

![Streaming diagram placeholder](../images/streaming.png)

To enable response streaming, set `streamingConfiguration.enableResponseStreaming` to `true` in the `protocolConfiguration.mcp` field when creating or updating your AgentCore gateway:

```bash


{
  "protocolConfiguration": {
    "mcp": {
      "streamingConfiguration": {
        "enableResponseStreaming": true
      }
    }
  }
}
```

Note: Enabling response streaming introduces a change to the response interceptor input contract. If you use response interceptors, review your interceptor logic to ensure compatibility with streaming responses. See [Response interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html#gateway-interceptors-types-streaming) with streaming enabled for details.

## How response streaming works

When response streaming is enabled and the client sends a request with Accept: text/event-stream, the gateway returns an SSE stream instead of a single JSON response. Events are delivered as they are received from the MCP server target.

If the client does not send Accept: text/event-stream, the gateway buffers the response and returns a single JSON response after the tool call completes. Intermediate events (progress, logging) are not delivered in this case.

## Workshop roadmap

| Step   | What you do                                                                              |
| ------ | ---------------------------------------------------------------------------------------- |
| **1**  | Set up the notebook (env vars, utilities, logging).                                      |
| **2**  | Create the gateway: Cognito inbound auth, IAM role, gateway with streaming enabled.      |
| **3**  | Deploy the `labstream` FastMCP server to AgentCore runtime.                              |
| **4**  | Wire it in as a gateway target (outbound OAuth, target creation, inbound token).         |
| **5**  | Backward compatibility — `Accept: application/json` only returns a single JSON response. |
| **6**  | Server-emitted progress notifications (`streaming_demo`).                                |
| **7**  | Mid-stream tool exception (`failing_demo`).                                              |
| **8**  | Server-emitted log events (`logging_demo`).                                              |
| **9**  | Long-running keep-alive via 30s progress (`keepalive_demo`).                             |
| **10** | Clean up.                                                                                |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)

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

The MCP server code is at [`gatewaylabproject/app/labstream/main.py`](../../../../gatewaylabproject/app/labstream/main.py). It exposes streaming tools: `streaming_demo` (progress events), `failing_demo` (mid-stream errors), `logging_demo` (log messages), and `keepalive_demo` (long-running with 30s progress heartbeats).

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
agentcore add agent \
  --name streaming_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/labstream \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
```

### Step 3: Create AgentCore gateway with Streaming Enabled (boto3)

> [!NOTE]
> The AgentCore CLI does not yet support `streamingConfiguration` or `supportedVersions`. This step uses a boto3 script.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/deploy_gateway.py \
  --name streaming-gateway \
  --streaming \
  --env-file scripts/streaming/.env
```

Export the gateway ID and URL (saved by the deploy script):

```bash
source scripts/streaming/.env
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
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'streaming_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 5: Create gateway Target (boto3)

```bash
uv run python scripts/deploy_target.py \
  --name streaming-mcp-server-target \
  --gateway-env-file scripts/streaming/.env
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore streaming interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, install Python dependencies (first time only):

```bash
uv sync
```

### Step 5: Backward compatibility — `Accept: application/json`

If the client does not send `Accept: text/event-stream`, the gateway buffers the response and returns a single JSON response after the tool call completes. Intermediate events (progress, logging) are not delivered in this case.

```bash
uv run python scripts/streaming/demo.py backward-compat
```

## Step 6: Server-emitted progress notifications

Example SSE stream response:

```text
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"auto-1","progress":1,"total":3,"message":"Loading data..."}}
event: message
data: {"jsonrpc":"2.0","method":"notifications/message","params":{"level":"info","logger":"analyzer","data":"Processing 10,000 records"}}
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"auto-1","progress":2,"total":3,"message":"Analyzing..."}}
event: message
data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progressToken":"auto-1","progress":3,"total":3,"message":"Complete"}}
event: message
data: {"jsonrpc":"2.0","id":"tool-call-1","result":{"content":[{"type":"text","text":"Analysis complete. Found 3 anomalies."}]}}
```

```bash
uv run python scripts/streaming/demo.py progress
```

## Step 7: Mid-stream tool exceptions

`failing_demo(steps=3)` emits two progress notifications then raises `RuntimeError`. The streaming response delivers the progress frames, then a `result.isError=true` content block as the final SSE frame.

```bash
uv run python scripts/streaming/demo.py failing
```

## Step 8: Server-emitted log events

`logging_demo()` emits one `notifications/message` per severity.

```bash
uv run python scripts/streaming/demo.py logging
```

## Step 9: Long-running keep-alive via 30s progress

`keepalive_demo(duration_seconds=N, interval_seconds=30, emit_progress=True)` sleeps for `duration_seconds`, emitting one progress frame every 30s. Used as the keep-alive pattern for tool calls that exceed the gateway's 15-min default request timeout.

> Demo runs at `duration_seconds=60` so the cell finishes quickly.

```bash
uv run python scripts/streaming/demo.py keepalive
```

This runs for ~60 seconds, printing a progress heartbeat every 30s and the final result at the end.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
uv run python scripts/cleanup_gateway.py \
  --name streaming-gateway \
  --env-file scripts/streaming/.env
```

Remove the CLI-managed agent and credentials:

```bash
agentcore remove agent --name streaming_mcp_server -y
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [MCP Progress Notifications](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-progress.html)
- [MCP Logging](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-logging.html)
