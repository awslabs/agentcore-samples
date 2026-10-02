# Context7 MCP Server through the Gateway

Front the public [Context7](https://context7.com) MCP server (`https://mcp.context7.com/mcp`) through an AgentCore Gateway as an `http.passthrough` target with `protocolType=MCP`. The gateway uses **no inbound authorization** and **JWT passthrough** for outbound: it forwards the caller's `Authorization` header (the client's own Context7 API key) to Context7 unchanged.

Context7 provides up-to-date library and framework documentation as MCP tools. It works unauthenticated at a lower rate limit, or with a `ctx7sk-...` API key for higher limits.

## Architecture

![arch](../images/architecture.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts `mcp.context7.com` as an `http.passthrough` MCP target; no inbound auth, forwards the caller's Authorization header outbound |
| Context7 MCP server | Public MCP server serving library documentation tools |

Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to `https://mcp.context7.com/{path}`.

## Tutorial details

| Item | Value |
| :-- | :-- |
| Target type | HTTP passthrough, `protocolType=MCP` |
| Endpoint | `https://mcp.context7.com/mcp` |
| Inbound auth | None (`authorizerType=NONE`) |
| Outbound auth | JWT passthrough (forwards the caller's `Authorization` header) |
| Gateway | Shared no-auth `context7-gateway` (no protocol type) |

> [!IMPORTANT]
> No-auth gateways accept unauthenticated requests from anyone who can reach the gateway URL. Use them only for public, rate-limited targets like Context7, or add your own access controls (for example, an interceptor). For a gateway that validates the inbound token before forwarding it, use `CUSTOM_JWT` inbound with `JWT_PASSTHROUGH` outbound instead.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Optional: a [Context7 API key](https://context7.com/docs/howto/api-keys) (`ctx7sk-...`) for higher rate limits

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Create the no-auth gateway

HTTP passthrough targets attach to a gateway that has no protocol type set. This script creates a no-auth gateway (`authorizerType=NONE`), or reuses it if it already exists. The gateway is shared with the [GitHub MCP](../github/) lab.

```bash
uv run python scripts/context7-passthrough/deploy_gateway.py
```

Capture the gateway URL:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/context7-passthrough/.env | cut -d= -f2)

echo "Gateway URL: $GATEWAY_URL"
```

### Step 2: Create the Context7 passthrough target

Attach `https://mcp.context7.com/mcp` as a passthrough target with `protocolType=MCP` and `JWT_PASSTHROUGH` outbound, so the caller's `Authorization` header is forwarded to Context7 unchanged.

```bash
uv run python scripts/context7-passthrough/deploy.py
```

The script calls `create_gateway_target` with this configuration:

```json
{
  "targetConfiguration": {
    "http": {
      "passthrough": {
        "endpoint": "https://mcp.context7.com/mcp",
        "protocolType": "MCP"
      }
    }
  },
  "credentialProviderConfigurations": [
    { "credentialProviderType": "JWT_PASSTHROUGH" }
  ],
  "metadataConfiguration": {
    "allowedRequestHeaders": [
      "Mcp-Session-Id",
      "Content-Type",
      "Accept"
    ],
    "allowedResponseHeaders": [
      "Mcp-Session-Id",
      "Content-Type"
    ]
  }
}
```

- `protocolType: MCP` gets a default schema, so no `schema` is needed (unlike `CUSTOM`).
- `JWT_PASSTHROUGH` forwards the inbound `Authorization` header outbound unchanged. The gateway does not store a Context7 key; the client supplies its own. This is supported on passthrough targets with `NONE` or `CUSTOM_JWT` inbound auth.
- MCP streamable-http issues an `Mcp-Session-Id` on `initialize` that the client echoes on later calls, and replies with SSE (`Content-Type: text/event-stream`). The target allowlists both as request and response headers so MCP clients can complete the handshake and parse the stream through the gateway. Without the `Content-Type` response allowlist, a client fails with `Unexpected token 'e', "event: mes"... is not valid JSON`.

## Demo

Call the Context7 MCP server through the gateway. With `authorizerType=NONE`, no gateway token is needed; an optional Context7 API key is forwarded as the `Authorization` header to Context7 for higher rate limits.

MCP over streamable-http requires an `initialize` handshake followed by `notifications/initialized` before any other call, and the server replies with SSE. The demo script runs that full handshake through the gateway, parses the SSE response, and lists the tools. It reads `GATEWAY_URL`, `TARGET_NAME`, and optional `CONTEXT7_API_KEY`:

```bash
uv run python scripts/context7-passthrough/invoke.py
```

Context7 exposes documentation tools such as `resolve-library-id` and `get-library-docs`. Without an API key, the script omits the `Authorization` header (Context7 serves unauthenticated requests at a lower rate limit).

### Explore with an MCP inspector

You can also connect an MCP client such as the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to the gateway target. Point it at `${GATEWAY_URL}/context7/mcp` (streamable-http transport); add `Authorization: Bearer <ctx7sk-...>` if you have a Context7 API key, or leave it empty for the unauthenticated tier. The inspector runs the `initialize`/`initialized` handshake for you, then lists and calls the documentation tools.

> [!IMPORTANT]
> The inspector parses the response by its `Content-Type`. The target must allowlist `Content-Type` (and `Mcp-Session-Id`) as response headers (Step 2); otherwise the gateway strips them and the inspector fails to complete the handshake or parse the SSE stream (`Unexpected token 'e', "event: mes"... is not valid JSON`).

![Context7 MCP server answering through the gateway](../images/context7.gif)

## Cleanup

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/context7-passthrough/cleanup.py
```

> [!NOTE]
> The `context7-gateway` is shared with the GitHub MCP lab. Cleanup removes only this lab's `context7` target. It deletes the shared gateway and its IAM role only when no targets remain (that is, the GitHub lab has also been cleaned up).

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [Context7](https://context7.com)
- [Context7 API keys](https://context7.com/docs/howto/api-keys)
