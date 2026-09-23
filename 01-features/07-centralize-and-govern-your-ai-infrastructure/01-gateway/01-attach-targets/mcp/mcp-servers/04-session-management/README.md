# AgentCore gateway — MCP Session management

## Overview

MCP sessions enable stateful interactions between clients and your AgentCore gateway. When sessions are enabled, the gateway generates a unique session identifier during initialization and maintains state across multiple requests, enabling advanced MCP features such as elicitation and sampling.

### Enable sessions on your gateway

To enable sessions, specify a sessionConfiguration in the protocolConfiguration.mcp field when creating or updating your gateway.

```bash
{
  "protocolConfiguration": {
    "mcp": {
      "sessionConfiguration": {
        "sessionTimeoutInSeconds": 3600
      }
    }
  }
}
```

The sessionTimeoutInSeconds parameter is optional. If omitted, the default timeout is 3600 seconds (1 hour). Valid range is 900 (15 minutes) to 28800 (8 hours). The timeout is absolute, calculated from the first initialize request.

![Sessions diagram placeholder](../images/session-management.png)

Note: When sessions are enabled on a gateway, you cannot include Mcp-Session-Id in the metadataConfiguration of a gateway target’s header propagation settings. The gateway manages session IDs internally. Attempting to do so returns an HTTP 400 Bad Request error.

### Benefits of using sessions

1.  Stateful MCP server target interactions:
    The gateway stores the MCP server target’s session ID and reuses it on subsequent tool calls. This avoids re-initialization on every request and enables targets to maintain context across calls.

2.  Faster responses with AgentCore runtime targets:
    When the target’s session is reused, AgentCore runtime doesn’t need to cold-start a new MCP server connection on each request, resulting in faster response times.

3.  Enables advanced MCP features:
    Sessions are a prerequisite for elicitation and sampling, which require tracking state across multiple requests.

4.  User-scoped security (authenticated gateways):
    For gateways with inbound authentication, sessions are bound to the verified user identity, preventing session hijacking.

## Workshop roadmap

| Step   | What you do                                                                           |
| ------ | ------------------------------------------------------------------------------------- |
| **1**  | Set up the notebook (env vars, utilities, logging).                                   |
| **2**  | Create the gateway: Cognito inbound auth, IAM role, gateway with sessions enabled.    |
| **3**  | Deploy the `labsession` FastMCP server to AgentCore runtime.                          |
| **4**  | Wire it in as a gateway target (outbound OAuth, target creation, inbound token).      |
| **5**  | Initialize a session — observe the `Mcp-Session-Id` header.                           |
| **6**  | Session continuity — `session_counter` increments across calls within one session.    |
| **7**  | Session isolation — a fresh `initialize` returns a new id and starts a fresh counter. |
| **8**  | Session-id error contract — missing / fake `Mcp-Session-Id`.                          |
| **9**  | Anti-hijacking note — sessions are scoped to the authenticated identity.              |
| **10** | Clean up.                                                                             |

## Tutorial Details

| Information          | Details                                                  |
| :------------------- | :------------------------------------------------------- |
| Tutorial type        | Interactive                                              |
| AgentCore components | AgentCore gateway, AgentCore identity, AgentCore runtime |
| gateway target type  | MCP server                                               |
| gateway features     | Sessions ON, Streaming OFF, no interceptor               |
| MCP transport        | Streamable HTTP, single JSON response                    |
| Inbound auth         | Cognito (M2M)                                            |
| Outbound auth        | Cognito (M2M) via OAuth2 credential provider             |
| SDK used             | boto3 + raw httpx                                        |

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

The MCP server code is at [`gatewaylabproject/app/labsession/main.py`](../../../../gatewaylabproject/app/labsession/main.py). `labsession` is intentionally minimal — `session_counter` keeps a per-session count, plus `getOrder` / `updateOrder` as sanity tools.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
agentcore add agent \
  --name session_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/labsession \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
```

### Step 3: Create AgentCore gateway with Sessions Enabled (boto3)

> [!NOTE]
> The AgentCore CLI does not yet support `sessionConfiguration` or `supportedVersions`. This step uses a boto3 script.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/deploy_gateway.py \
  --name session-gateway \
  --sessions \
  --env-file scripts/sessions/.env
```

Export the gateway ID and URL (saved by the deploy script):

```bash
source scripts/sessions/.env
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
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'session_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 5: Create gateway Target (boto3)

```bash
uv run python scripts/deploy_target.py \
  --name session-mcp-server-target \
  --gateway-env-file scripts/sessions/.env
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore sessions interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, install Python dependencies (first time only):

```bash
uv sync
```

## Step 5: Initialize and capture the `Mcp-Session-Id`

A session-enabled gateway issues a `Mcp-Session-Id` on the response to the first `initialize`.

```bash
uv run python scripts/sessions/demo.py initialize
```

## Step 6: Session continuity — counter increments

`session_counter` returns `{session_id, count}` where `session_id` is fastmcp’s _upstream_ per-process session id and `count` is incremented per call within the same session. Repeated calls on the same `Mcp-Session-Id` see the count grow.

```bash
uv run python scripts/sessions/demo.py continuity
```

## Step 7: Session isolation — fresh `initialize` resets the counter

A second `initialize` from the same client returns a different `Mcp-Session-Id`. Calls against this new id start with `count=1` — separate state, separate upstream session.

```bash
uv run python scripts/sessions/demo.py isolation
```

## Step 8: Session-id error contract

The gateway’s session validator handles three lookup-fail cases:

| Probe                                                         | Expected | Body                                     |
| ------------------------------------------------------------- | -------- | ---------------------------------------- |
| Missing `Mcp-Session-Id`                                      | HTTP 400 | `Missing required Mcp-Session-Id header` |
| Random / never-issued `Mcp-Session-Id`                        | HTTP 404 | `Session not found or expired`           |
| Real `Mcp-Session-Id` from a different authenticated identity | HTTP 404 | `Session not found or expired`           |

```bash
uv run python scripts/sessions/demo.py error-contract
```

## Step 9: Sessions are scoped to the authenticated identity

Sessions are scoped to the authenticated user. The AgentCore gateway derives the user identity from the authorization context, the JWT bearer token for OAuth ingress or the IAM credentials for AWS_IAM ingress, and validates that every request within a session originates from the same user.

Practical: a different Cognito M2M client (different `client_id` claim in the JWT), even with a valid token for the same gateway, cannot reuse another client’s `Mcp-Session-Id`. Cross-identity reuse returns HTTP 404 `Session not found or expired` — the gateway treats the session as nonexistent rather than leaking that it belongs to someone else.

## Step 10: Faster responses with AgentCore runtime targets

When the target’s session is reused, AgentCore runtime doesn’t need to cold-start a new MCP server connection on each request, resulting in faster response times.

Observe the time difference between first and subsequent invokes.

```bash
uv run python scripts/sessions/demo.py performance
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
uv run python scripts/cleanup_gateway.py \
  --name session-gateway \
  --env-file scripts/sessions/.env
```

Remove the CLI-managed agent and credentials:

```bash
agentcore remove agent --name session_mcp_server -y
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [MCP Sessions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-mcp-sessions.html)
