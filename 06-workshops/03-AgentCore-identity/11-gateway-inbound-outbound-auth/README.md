# AgentCore Identity: Gateway Inbound and Outbound Auth (Cognito)

## Overview

This sample shows how to secure an **AgentCore Gateway** with both inbound and outbound authentication using Amazon Cognito as the Identity Provider.

- **Inbound Auth**: The gateway endpoint is protected by a Cognito JWT (`CUSTOM_JWT` authorizer). Callers must present a valid bearer token.
- **Outbound Auth**: The gateway authenticates to an upstream MCP server using OAuth2 client credentials (configured declaratively in `mcp.json` — no agent code changes required).

The agent connects to the gateway to discover and call tools. The gateway handles all auth on behalf of the agent.

### Architecture

```
Caller
  │  Authorization: Bearer <Cognito JWT>
  ▼
AgentCore Gateway  ──validates JWT──▶  Cognito User Pool
  │
  │  OAuth2 client credentials (outbound)
  ▼
Upstream MCP Server  (e.g., internal tool service, third-party API)
  ▲
  │  tools response
AgentCore Runtime Agent
```

### Tutorial Details

| Information         | Details                                                 |
|:--------------------|:--------------------------------------------------------|
| Tutorial type       | CLI walkthrough                                         |
| Agent type          | Single (with Gateway)                                   |
| Agentic Framework   | Strands Agents                                          |
| LLM model           | Anthropic Claude Haiku 4.5                              |
| Inbound Auth        | Amazon Cognito (CUSTOM_JWT) on Gateway                  |
| Outbound Auth       | OAuth2 client credentials on Gateway Target             |
| Example complexity  | Medium                                                  |
| CLI tool            | `agentcore` (npm: `@aws/agentcore`)                     |

---

## Prerequisites

- **Node.js** 20.x or later
- **Python** 3.10+
- **uv** ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured
- **AgentCore CLI** installed:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

- **Amazon Bedrock model access**: Enable `claude-haiku-4-5` in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/models)
- **An MCP server endpoint** that requires OAuth2 (or use `--outbound-auth none` for testing)

---

## Step 1: Install Setup Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2: Set Up Cognito (Inbound IdP)

```bash
python setup_cognito.py
```

This creates:
- A Cognito User Pool with a test user (`testuser` / `AgentCoreTest1!`)
- A **user-facing app client** (for callers authenticating with the gateway)
- An **agent-facing app client** (used by the CLI to create a managed credential)
- Saves all values to `cognito_config.json`

Take note of the values printed at the end — you will need them in Step 4:

```
--discovery-url    https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/openid-configuration
--allowed-clients  <user_client_id>,<agent_client_id>
--client-id        <agent_client_id>
--client-secret    <from cognito_config.json>
```

---

## Step 3: Create the AgentCore Project

```bash
agentcore create --name GatewayAuthDemo --defaults --no-agent
cd GatewayAuthDemo
```

Set your deployment target (the CLI creates an empty `aws-targets.json`):

```bash
cat > agentcore/aws-targets.json << 'EOF'
[{"name":"default","description":"Default deployment target","account":"YOUR_AWS_ACCOUNT_ID","region":"us-east-1"}]
EOF
```

> Replace `YOUR_AWS_ACCOUNT_ID` with your 12-digit AWS account ID. Find it with `aws sts get-caller-identity --query Account --output text`.

---

## Step 4: Add the Gateway with Cognito JWT Inbound Auth

Replace the placeholder values with those from `cognito_config.json`:

```bash
agentcore add gateway \
  --name MyGateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url YOUR_COGNITO_DISCOVERY_URL \
  --allowed-clients YOUR_USER_CLIENT_ID,YOUR_AGENT_CLIENT_ID \
  --client-id YOUR_AGENT_CLIENT_ID \
  --client-secret YOUR_AGENT_CLIENT_SECRET
```

The CLI automatically creates a **managed OAuth credential** so the agent can obtain Bearer tokens to call the gateway. This credential appears in `agentcore/agentcore.json` as `"managed": true`.

---

## Step 5: Add Gateway Target

Add the MCP server (deployed in Step 1) as a gateway target. Use the endpoint URL from `mcp_server_config.json`:

```bash
agentcore add gateway-target \
  --name MyTools \
  --type mcp-server \
  --endpoint YOUR_MCP_SERVER_ENDPOINT \
  --gateway MyGateway
```

> Replace `YOUR_MCP_SERVER_ENDPOINT` with the endpoint printed by `setup_mcp_server.py` (e.g. `https://abc123.execute-api.us-east-1.amazonaws.com/mcp`).
>
> To add OAuth outbound auth to the target (if your MCP server requires it), use `--outbound-auth oauth --credential-name YOUR_CREDENTIAL`.

---

## Step 6: Add the Agent

```bash
agentcore add agent \
  --name MyAgent \
  --type byo \
  --code-location ../app/MyAgent \
  --entrypoint main.py \
  --language Python \
  --framework Strands \
  --model-provider Bedrock \
  --authorizer-type CUSTOM_JWT \
  --discovery-url YOUR_COGNITO_DISCOVERY_URL \
  --allowed-clients YOUR_USER_CLIENT_ID
```

Replace `YOUR_COGNITO_DISCOVERY_URL` and `YOUR_USER_CLIENT_ID` with values from `cognito_config.json`. This configures JWT inbound auth on the runtime at deploy time.

---

## Step 7: Deploy

```bash
agentcore deploy -y
```

Check status:

```bash
agentcore status
```

---

## Step 8: Post-Deploy Configuration

Run this post-deploy script to apply JWT inbound auth on the runtime, set the gateway URL environment variable, attach IAM permissions for outbound credential retrieval, and ensure the managed gateway credential exists:

```bash
cd ..
python configure_inbound_auth.py
```

Wait ~30 seconds for changes to propagate.

---

## Step 9: Test Inbound and Outbound Auth

```bash
cd ..
python invoke.py "What tools do you have available?"
```

Expected output:

```
[Test 1] Invoking WITHOUT bearer token (expect AccessDeniedException)...
  Correctly rejected: An error occurred (AccessDeniedException) ...

[Test 2] Invoking WITH Cognito bearer token (expect success)...
  Token obtained (first 20 chars): eyJraWQiOiJxT...

Agent response:
I have access to the following tools through the gateway: [tool list]
```

---

## How Outbound Auth Works

When the agent calls a gateway tool:

1. **Agent → Gateway**: agent presents its managed Cognito credential (Bearer token) — handled automatically by `GatewayClient`
2. **Gateway validates** the inbound JWT against Cognito
3. **Gateway → MCP Server**: gateway exchanges the stored OAuth2 client credentials for an access token and forwards the request
4. **MCP Server** responds with tool results

The agent code has no knowledge of the upstream credentials — they are managed entirely within the gateway.

---

## Streamlit UI (Optional)

For an interactive browser-based experience instead of the CLI:

```bash
pip install streamlit
cd ..
streamlit run streamlit_app.py
```

Log in, then use the chat interface to test gateway tools (get_time, echo). Clear the Bearer Token field in the sidebar to test 403 rejection.

---

## Step 10: Cleanup

```bash
cd GatewayAuthDemo
agentcore remove gateway-target --name MyTools --force
agentcore remove gateway --name MyGateway --force
agentcore remove agent --name MyAgent --force
```

Delete Cognito resources:

```python
import boto3, json

with open("../cognito_config.json") as f:
    config = json.load(f)

boto3.client("cognito-idp", region_name=config["region"]).delete_user_pool(
    UserPoolId=config["pool_id"]
)
print("Cognito User Pool deleted.")
```

---
