# Claude Managed Agents through the Gateway

Front [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) through an AgentCore Gateway as an `http.passthrough` target with `protocolType=CUSTOM`. The gateway is a secure proxy: it validates the inbound caller, then forwards the caller's own Claude API key (`x-api-key`) and request body to `https://api.anthropic.com` through header propagation. The gateway does not store or inject the Claude key.

Claude Managed Agents is a pre-built, configurable agent harness that runs in managed infrastructure: you create an agent, an environment, and a session, then send events and stream the agent's work back over SSE. This tutorial runs that full flow through the gateway.

## Architecture

![arch](../../images/agents.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts `api.anthropic.com` as an `http.passthrough` CUSTOM target; validates the inbound JWT and forwards `x-api-key` via header propagation |
| AgentCore Identity | Not used for outbound here; the client supplies its own Claude key |
| Microsoft Entra ID | Issues the inbound JWT that authorizes the caller to the gateway |
| Claude Managed Agents | The managed agent harness on `api.anthropic.com` (agent, environment, session, events) |

Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to `https://api.anthropic.com/{path}`. For example `POST {GATEWAY_URL}/claude-managed-agents/v1/agents` reaches `POST https://api.anthropic.com/v1/agents`.

## Tutorial details

| Item | Value |
| :-- | :-- |
| Target type | HTTP passthrough, `protocolType=CUSTOM` |
| Endpoint | `https://api.anthropic.com` |
| Inbound auth | Microsoft Entra ID (`CUSTOM_JWT`) |
| Outbound auth | Header propagation of the client's `x-api-key` (no credential provider) |
| Gateway | Shared `runtime-agents-gateway` (no protocol type) |
| SDKs shown | boto3, Python requests, Anthropic SDK |

> [!NOTE]
> HTTP passthrough targets do not support an `API_KEY` credential provider for outbound auth. The working pattern is header propagation: the client sends its own `x-api-key`, allowlisted via `metadataConfiguration.allowedRequestHeaders`, and the gateway forwards it.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A [Claude API key](https://platform.claude.com/settings/keys) with Claude Managed Agents beta access
- A Microsoft Entra ID gateway app registration. This tutorial reuses the gateway from the [A2A agent](../../a2a-agents/agentcore-runtime/) and [HTTP agent](../http-runtime-agents/) labs; follow their Step 1 to register the gateway app and record `MICROSOFT_TENANT_ID` and `MICROSOFT_GATEWAY_CLIENT_ID`.

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Export credentials

```bash
export MICROSOFT_TENANT_ID=""          # Directory (tenant) ID
export MICROSOFT_GATEWAY_CLIENT_ID=""  # Gateway app (client) ID
export CLAUDE_API_KEY=""               # Your Claude API key

export ENTRA_DISCOVERY_URL="https://login.microsoftonline.com/$MICROSOFT_TENANT_ID/.well-known/openid-configuration"
```

### Step 2: Create or reuse the gateway

HTTP passthrough targets attach to a gateway that has no protocol type set. This script creates that gateway with Entra ID inbound auth, or reuses it if it already exists.

```bash
uv run python scripts/managed-agents-custom/deploy_gateway.py \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"
```

> [!NOTE]
> This gateway (`runtime-agents-gateway`) is shared with the A2A and HTTP runtime-agent labs. If you already created it there, this script detects the existing gateway and reuses it.

Capture the gateway URL written by the script:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/managed-agents-custom/.env | cut -d= -f2)

echo "Gateway URL: $GATEWAY_URL"
```

### Step 3: Create the passthrough target

Attach `https://api.anthropic.com` as a CUSTOM passthrough target. The target has no credential provider; it allowlists `x-api-key` (plus the `anthropic-*` headers) so the client's Claude key is forwarded outbound. CUSTOM protocol targets must provide a schema to use guardrails, so the script ships an OpenAPI schema ([`scripts/managed-agents-custom/managed-agents-schema.yaml`](../../../../../gatewaylabproject/scripts/managed-agents-custom/managed-agents-schema.yaml)) and attaches it inline.

```bash
uv run python scripts/managed-agents-custom/deploy.py
```

The script calls `create_gateway_target` with this configuration:

```json
{
  "targetConfiguration": {
    "http": {
      "passthrough": {
        "endpoint": "https://api.anthropic.com",
        "protocolType": "CUSTOM",
        "schema": {
          "source": {
            "inlinePayload": "<openapi-schema-string>"
          }
        }
      }
    }
  },
  "metadataConfiguration": {
    "allowedRequestHeaders": [
      "x-api-key",
      "anthropic-version",
      "anthropic-beta",
      "content-type"
    ]
  }
}
```

- `protocolType: CUSTOM` marks this as a proprietary protocol; unlike `MCP` and `A2A` (which get a default schema), CUSTOM targets must supply a `schema` to enable policy-engine features such as guardrails.
- The schema `source` is either `inlinePayload` (the schema content as a string, used here) or `s3` (an S3 URI such as `s3://DOC-EXAMPLE-BUCKET/service-schema.yaml`).

## Set up and run a Managed Agent

The Managed Agents flow is: create an **agent**, create an **environment**, start a **session**, send a user **event**, and stream the response. Every request goes through the gateway at `${GATEWAY_URL}/claude-managed-agents/v1/...`, carrying the Entra JWT (`Authorization: Bearer`) and your Claude key (`x-api-key`). All Managed Agents requests require the `managed-agents-2026-04-01` beta header.

First acquire an Entra ID gateway token. Reuse the OBO lab's callback server for the browser sign in (from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory):

```bash
python3 scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID "<gateway-app-client-secret>"

export BEARER_TOKEN=$(curl -sS http://localhost:9090/token \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

### Option 1: curl

```bash
export BASE="${GATEWAY_URL}/claude-managed-agents"

# 1. Create an agent
AGENT_ID=$(curl -sS "$BASE/v1/agents" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "x-api-key: $CLAUDE_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -H "content-type: application/json" \
  -d '{"name":"Coding Assistant","model":"claude-opus-4-8","system":"You are a helpful coding assistant. Write clean, well-documented code.","tools":[{"type":"agent_toolset_20260401"}]}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

# 2. Create an environment
ENVIRONMENT_ID=$(curl -sS "$BASE/v1/environments" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "x-api-key: $CLAUDE_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -H "content-type: application/json" \
  -d '{"name":"quickstart-env","config":{"type":"cloud","networking":{"type":"unrestricted"}}}' \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

# 3. Start a session
SESSION_ID=$(curl -sS "$BASE/v1/sessions" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "x-api-key: $CLAUDE_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -H "content-type: application/json" \
  -d "{\"agent\":\"$AGENT_ID\",\"environment_id\":\"$ENVIRONMENT_ID\",\"title\":\"Quickstart session\"}" \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")

# 4. Send a user message
curl -sS "$BASE/v1/sessions/$SESSION_ID/events" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "x-api-key: $CLAUDE_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01" \
  -H "content-type: application/json" \
  -d '{"events":[{"type":"user.message","content":[{"type":"text","text":"Create a Python script that generates the first 20 Fibonacci numbers and saves them to fibonacci.txt"}]}]}'
```

To stream the agent's events back over SSE, use the Anthropic SDK (Option 3). The SSE stream must be opened **before** the user event is sent, which the SDK handles for you.

### Option 2: Python (requests)

Install dependencies once, then run the demo:

```bash
uv sync
uv run python scripts/managed-agents-custom/invoke.py
```

It runs the agent -> environment -> session -> events flow and confirms the message was accepted. It reads `GATEWAY_URL`, `TARGET_NAME`, `BEARER_TOKEN`, and `CLAUDE_API_KEY`. To stream the agent's events back, use the Anthropic SDK (Option 3), which opens the SSE stream before sending the event as the API requires.

### Option 3: Anthropic SDK

```bash
uv run python scripts/managed-agents-custom/sdk_demo.py
```

Same flow using the Anthropic Python SDK pointed at the gateway target. The SDK sends the Entra JWT as `Authorization: Bearer` (via `auth_token`) and the Claude key as an `x-api-key` default header.

![Claude Managed Agent running through the gateway](../images/claude-demo.gif)

## Cleanup

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/managed-agents-custom/cleanup.py
```

> [!NOTE]
> The gateway is shared with the A2A and HTTP runtime-agent labs. Cleanup removes only this lab's `claude-managed-agents` target. It deletes the shared gateway and its IAM role only when no targets remain. There is no credential provider to delete (the client supplied its own Claude key), and no Entra app to remove here (it is shared with the runtime-agent labs).

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [Claude Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview)
- [Claude Managed Agents quickstart](https://platform.claude.com/docs/en/managed-agents/quickstart)
