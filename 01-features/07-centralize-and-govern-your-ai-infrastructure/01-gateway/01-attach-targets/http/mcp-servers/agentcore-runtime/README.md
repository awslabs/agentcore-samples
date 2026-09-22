# MCP Server on AgentCore Runtime through the Gateway

Front an MCP server hosted on Amazon Bedrock AgentCore Runtime through the gateway as an `http.passthrough` target with `protocolType=MCP`. The passthrough `endpoint` is the runtime's invocation URL. The gateway uses **no inbound authorization** and **JWT passthrough** for outbound: it forwards the caller's `Authorization` header (a Microsoft Entra ID bearer) to the runtime, whose own `CUSTOM_JWT` inbound auth validates it.

This tutorial uses the elicitation MCP server (`app/labelicitation/`) deployed on AgentCore Runtime as `elicitation_mcp_jwt`.

> [!NOTE]
> This tutorial attaches the runtime via an `http.passthrough` target pointed at the runtime invocation URL. The more conventional way to attach a runtime-hosted server is an `http.agentcoreRuntime` target (the gateway resolves the runtime by ARN). This lab intentionally demonstrates the passthrough approach on a shared no-auth gateway.

## Architecture

![arch](../images/architecture.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts the runtime invocation URL as an `http.passthrough` MCP target; no inbound auth, forwards the caller's Authorization and session-id headers outbound |
| AgentCore Runtime | Hosts the elicitation MCP server (`CUSTOM_JWT` inbound); validates the forwarded Entra ID bearer |
| Microsoft Entra ID | Issues the bearer the runtime validates |


Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to the runtime invocation URL.

## Tutorial details

| Item | Value |
| :-- | :-- |
| Target type | HTTP passthrough, `protocolType=MCP` |
| Endpoint | The runtime invocation URL (captured at deploy time) |
| Inbound auth | None (`authorizerType=NONE`) |
| Outbound auth | JWT passthrough (forwards the caller's `Authorization` header) |
| Runtime inbound auth | Microsoft Entra ID (`CUSTOM_JWT`, validates `api://<runtime-client-id>`) |
| Gateway | Shared no-auth `context7-gateway` (no protocol type) |
| MCP server | Elicitation MCP server (`elicitation_mcp_jwt`) on AgentCore Runtime |

> [!IMPORTANT]
> No-auth gateways accept unauthenticated requests from anyone who can reach the gateway URL. The runtime behind this target still enforces its own `CUSTOM_JWT` inbound auth, so a forwarded token is required to reach it. For the gateway itself to validate the token before forwarding, use `CUSTOM_JWT` inbound with `JWT_PASSTHROUGH` outbound instead.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A Microsoft Entra ID (Azure AD) tenant with permission to register applications and grant admin consent (the runtime validates an Entra ID bearer)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Register a Microsoft Entra ID application

The runtime validates an Entra ID bearer on inbound. Register one application that callers authenticate against and the runtime validates as the token audience.

1. Go to [entra.microsoft.com](https://entra.microsoft.com) -> **App registrations** -> **New registration**.
2. Name: `MCP-Runtime-Resource`, Single tenant, **Register**.
3. From **Overview**, copy **Application (client) ID** -> `MICROSOFT_RUNTIME_CLIENT_ID` and **Directory (tenant) ID** -> `MICROSOFT_TENANT_ID`.
4. **Certificates & secrets** -> **+ New client secret**. Copy the **Value** -> `MICROSOFT_RUNTIME_CLIENT_SECRET` (the callback server uses it to complete the sign-in code exchange).
5. **Expose an API** -> set **Application ID URI** (accept the default `api://<runtime-client-id>`) -> **+ Add a scope** named `access_as_user` (consent: Admins and users, Enabled).
6. **Authentication** -> **+ Add a platform** -> **Web** -> redirect URI `http://localhost:9090/oauth2/callback` -> **Configure**. The OBO lab's callback server signs the user in against this app and captures the token.
7. **API permissions** -> **+ Add a permission** -> **My APIs** tab -> select `MCP-Runtime-Resource` (this same app) -> **Delegated permissions** -> add `access_as_user`. Then **Grant admin consent for [tenant]** -> **Yes**, so the sign-in can issue a token for the runtime audience without a consent prompt.

```bash
export MICROSOFT_TENANT_ID=""             # Directory (tenant) ID
export MICROSOFT_RUNTIME_CLIENT_ID=""     # Runtime app (client) ID
export MICROSOFT_RUNTIME_CLIENT_SECRET="" # Runtime app client secret

export ENTRA_DISCOVERY_URL="https://login.microsoftonline.com/$MICROSOFT_TENANT_ID/.well-known/openid-configuration"
```

> [!NOTE]
> By default, Entra ID issues **v1.0** access tokens (issuer `https://sts.windows.net/{tenant}/`). The runtime inbound discovery URL in this tutorial uses the v1.0 endpoint to match.

### Step 1b: Deploy the elicitation MCP server on AgentCore Runtime

The MCP server code is at [`gatewaylabproject/app/labelicitation/`](../../../../../gatewaylabproject/app/labelicitation/). Register and deploy it with Entra ID `CUSTOM_JWT` inbound auth, validating the runtime app audience.

```bash
agentcore add agent \
  --name elicitation_mcp_jwt \
  --type byo \
  --build CodeZip \
  --language Python \
  --protocol MCP \
  --code-location app/labelicitation \
  --entrypoint main.py \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_RUNTIME_CLIENT_ID"

agentcore deploy
```

Capture the runtime invocation URL:

```bash
export RUNTIME_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'elicitation_mcp_jwt'))
")

echo "Runtime URL: $RUNTIME_URL"
```

### Step 2: Create the no-auth gateway

HTTP passthrough targets attach to a gateway that has no protocol type set. This script creates a no-auth gateway (`authorizerType=NONE`), or reuses it if it already exists. The gateway is shared with the [Context7](../context7/) and [GitHub](../github/) MCP labs.

```bash
uv run python scripts/runtime-mcp-passthrough/deploy_gateway.py
```

Capture the gateway URL:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/runtime-mcp-passthrough/.env | cut -d= -f2)
export GATEWAY_ID=$(grep GATEWAY_ID scripts/runtime-mcp-passthrough/.env | cut -d= -f2)

echo "Gateway URL: $GATEWAY_URL"
```

### Step 3: Create the passthrough target

Attach the runtime invocation URL as a passthrough target with `protocolType=MCP` and `JWT_PASSTHROUGH` outbound. The target also allowlists the runtime session-id header so it is forwarded alongside `Authorization`.

```bash
uv run python scripts/runtime-mcp-passthrough/deploy.py --endpoint "$RUNTIME_URL"
```

The script calls `create_gateway_target` with this configuration:

```json
{
  "targetConfiguration": {
    "http": {
      "passthrough": {
        "endpoint": "<RUNTIME_URL>",
        "protocolType": "MCP"
      }
    }
  },
  "credentialProviderConfigurations": [
    { "credentialProviderType": "JWT_PASSTHROUGH" }
  ],
  "metadataConfiguration": {
    "allowedRequestHeaders": [
      "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id",
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
- `JWT_PASSTHROUGH` forwards the inbound `Authorization` header outbound unchanged. The runtime's `CUSTOM_JWT` inbound auth validates the forwarded Entra ID bearer.
- AgentCore Runtime also requires the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header, so the target allowlists it via `metadataConfiguration`.
- MCP streamable-http issues an `Mcp-Session-Id` on `initialize` that the client echoes on later calls, so the target allowlists it as both a request and a response header. Without the response allowlist the gateway strips the session id and the handshake cannot complete.
- MCP responses are SSE (`Content-Type: text/event-stream`), so the target also allowlists `Content-Type` as a response header. Without it the gateway drops the content type and MCP clients (such as inspectors) try to JSON-decode the SSE stream and fail with `Unexpected token 'e', "event: mes"... is not valid JSON`. Raw `curl` is unaffected because it prints the body regardless.

The `elicitation-runtime` target should reach `READY`.

## Demo

Call the runtime MCP server through the gateway. The runtime enforces `CUSTOM_JWT` inbound, so acquire an Entra ID **user** token for the runtime app audience and send it as the `Authorization` header; the gateway forwards it. Sign in with the OBO lab's callback server, passing `--scope` so the token is issued for the runtime audience. From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_RUNTIME_CLIENT_ID $MICROSOFT_RUNTIME_CLIENT_SECRET \
  --scope "api://$MICROSOFT_RUNTIME_CLIENT_ID/access_as_user openid profile email"

export BEARER_TOKEN="<BearerToken>"
```

Sign in when the browser opens, then capture the token:

```bash
export BEARER_TOKEN=$(curl -sS http://localhost:9090/token \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

Run the demo. It performs the full MCP streamable-http handshake through the gateway (`initialize` -> `notifications/initialized` -> `tools/list`), pinning every request to one runtime microvm with the AgentCore session id and echoing the MCP `Mcp-Session-Id` the server issues on `initialize`. It reads `GATEWAY_URL`, `TARGET_NAME`, and `BEARER_TOKEN`:

```bash
uv run python scripts/runtime-mcp-passthrough/invoke.py
```

The elicitation server returns its tools (`book_room`, `cancel_with_confirm`, `log_expense`, ...).

> [!NOTE]
> MCP requires `initialize` then `notifications/initialized` before any other call; a bare `tools/list` returns `400`. Responses are SSE (`text/event-stream`), which the script parses. Doing this by hand with `curl` means a three-step handshake carrying both session-id headers, which is why the script is the simpler path.

### Explore with an MCP inspector

You can also connect an MCP client such as the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) to the gateway target. Point it at `${GATEWAY_URL}/elicitation-runtime` (streamable-http transport) and add two headers: `Authorization: Bearer <BEARER_TOKEN>` (the runtime-audience Entra token) and `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` set to a value of 33+ characters. The inspector runs the `initialize`/`initialized` handshake for you, then lists and calls the elicitation tools.

> [!IMPORTANT]
> The inspector parses the response by its `Content-Type`. The target must allowlist `Content-Type` as a response header (Step 3); otherwise the gateway strips it and the inspector fails to parse the SSE stream with `Unexpected token 'e', "event: mes"... is not valid JSON`.

![Elicitation MCP server answering through the gateway](../images/runtime.gif)

## Cleanup

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/runtime-mcp-passthrough/cleanup.py
```

> [!NOTE]
> The `context7-gateway` is shared with the Context7 and GitHub MCP labs. Cleanup removes only this lab's `elicitation-runtime` target. It deletes the shared gateway and its IAM role only when no targets remain.

Remove the runtime:

```bash
agentcore remove agent --name elicitation_mcp_jwt -y
agentcore deploy
```

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
