# GitHub MCP Server through the Gateway

Front the [GitHub MCP server](https://github.com/github/github-mcp-server) (`https://api.githubcopilot.com/mcp/`) through an AgentCore Gateway as an `http.passthrough` target with `protocolType=MCP`. The gateway uses **no inbound authorization** and **JWT passthrough** for outbound: it forwards the caller's `Authorization` header (the client's own GitHub token) to the GitHub MCP server unchanged.

The GitHub MCP server exposes GitHub repositories, issues, pull requests, and more as MCP tools. It requires a GitHub token with appropriate access.

## Architecture

![arch](../images/architecture.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts `api.githubcopilot.com` as an `http.passthrough` MCP target; no inbound auth, forwards the caller's Authorization header outbound |
| GitHub MCP server | Hosted MCP server serving GitHub repository, issue, and pull-request tools |

Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to `https://api.githubcopilot.com/mcp/{path}`.

## Tutorial details

| Item | Value |
| :-- | :-- |
| Target type | HTTP passthrough, `protocolType=MCP` |
| Endpoint | `https://api.githubcopilot.com/mcp/` |
| Inbound auth | None (`authorizerType=NONE`) |
| Outbound auth | JWT passthrough (forwards the caller's `Authorization` header) |
| Gateway | Shared no-auth `context7-gateway` (no protocol type) |

> [!IMPORTANT]
> No-auth gateways accept unauthenticated requests from anyone who can reach the gateway URL. Use them only for token-forwarding targets like this one, or add your own access controls (for example, an interceptor). For a gateway that validates the inbound token before forwarding it, use `CUSTOM_JWT` inbound with `JWT_PASSTHROUGH` outbound instead.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A GitHub token with access to the GitHub MCP server (a personal access token or GitHub Copilot access)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Create the no-auth gateway

HTTP passthrough targets attach to a gateway that has no protocol type set. This script creates a no-auth gateway (`authorizerType=NONE`), or reuses it if it already exists. The gateway is shared with the [Context7 MCP](../context7/) lab.

```bash
uv run python scripts/github-mcp-passthrough/deploy_gateway.py
```

Capture the gateway URL:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/github-mcp-passthrough/.env | cut -d= -f2)

echo "Gateway URL: $GATEWAY_URL"
```

### Step 2: Create the GitHub passthrough target

Attach `https://api.githubcopilot.com/mcp/` as a passthrough target with `protocolType=MCP` and `JWT_PASSTHROUGH` outbound, so the caller's `Authorization` header is forwarded to the GitHub MCP server unchanged.

```bash
uv run python scripts/github-mcp-passthrough/deploy.py
```

The script calls `create_gateway_target` with this configuration:

```json
{
  "targetConfiguration": {
    "http": {
      "passthrough": {
        "endpoint": "https://api.githubcopilot.com/mcp/",
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
- `JWT_PASSTHROUGH` forwards the inbound `Authorization` header outbound unchanged. The gateway does not store a GitHub token; the client supplies its own. This is supported on passthrough targets with `NONE` or `CUSTOM_JWT` inbound auth.
- MCP streamable-http issues an `Mcp-Session-Id` on `initialize` that the client echoes on later calls, and replies with SSE (`Content-Type: text/event-stream`). The target allowlists both as request and response headers so MCP clients can complete the handshake and parse the stream through the gateway. Without the `Content-Type` response allowlist, a client fails with `Unexpected token 'e', "event: mes"... is not valid JSON`.


## Demo

Call the GitHub MCP server through the gateway. With `authorizerType=NONE`, no gateway token is needed; your GitHub token is forwarded as the `Authorization` header to GitHub.

MCP over streamable-http requires an `initialize` handshake followed by `notifications/initialized` before any other call, and the server replies with SSE. The demo script runs that full handshake through the gateway, parses the SSE response, and lists the tools. It reads `GATEWAY_URL`, `TARGET_NAME`, and `GITHUB_TOKEN`:

```bash
export GITHUB_TOKEN="<your-github-pat>"

uv run python scripts/github-mcp-passthrough/invoke.py
```

The GitHub MCP server exposes tools for repositories, issues, and pull requests. A valid GitHub token is required (unlike Context7, the GitHub MCP server has no unauthenticated tier).

![GitHub MCP server answering through the gateway](../images/github.gif)

> [!IMPORTANT]
> The inspector parses the response by its `Content-Type`. The target must allowlist `Content-Type` (and `Mcp-Session-Id`) as response headers (Step 2); otherwise the gateway strips them and the inspector fails to complete the handshake or parse the SSE stream (`Unexpected token 'e', "event: mes"... is not valid JSON`).

## Cleanup

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/github-mcp-passthrough/cleanup.py
```

> [!NOTE]
> The `context7-gateway` is shared with the Context7 MCP lab. Cleanup removes only this lab's `github` target. It deletes the shared gateway and its IAM role only when no targets remain (that is, the Context7 lab has also been cleaned up).

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [GitHub MCP server](https://github.com/github/github-mcp-server)
