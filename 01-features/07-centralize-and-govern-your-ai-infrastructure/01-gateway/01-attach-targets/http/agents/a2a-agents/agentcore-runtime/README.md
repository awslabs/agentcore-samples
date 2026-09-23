# A2A Agent on AgentCore Runtime with Entra ID OBO

Attach an Agent-to-Agent (A2A) protocol agent hosted on Amazon Bedrock AgentCore Runtime to your gateway as an `http.agentcoreRuntime` target, with **Microsoft Entra ID** for inbound auth and **OBO (on-behalf-of) token exchange** for outbound auth to the runtime.

A caller signs in with Entra ID and sends the gateway an app scoped access token. The gateway validates it, then exchanges it on behalf of the user for a token scoped to the runtime, and forwards the request. The user's identity is preserved end to end, from caller through gateway to runtime.

This tutorial uses a self contained A2A monitoring agent (built with the `a2a-sdk`) that answers questions about CloudWatch logs and metrics using local boto3 tools.

## Architecture

![arch](../../images/agents.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts the runtime agent as an HTTP target with path based routing; validates the inbound Entra token and performs the OBO exchange |
| AgentCore Runtime | Hosts the A2A monitoring agent (protocol `A2A`); validates the OBO exchanged token on inbound |
| AgentCore Identity | Stores the OBO credential provider the gateway uses for the token exchange |
| Microsoft Entra ID | Issues the inbound user token and performs the OBO token exchange |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `global.anthropic.claude-haiku-4-5-20251001-v1:0`
- A Microsoft Entra ID (Azure AD) tenant with permission to register applications and grant admin consent

> [!NOTE]
> This tutorial deploys real Entra ID app registrations and AgentCore resources.

## Identifiers used in this tutorial

This tutorial registers **two** Entra ID applications. Keep their IDs straight:

| Variable | Meaning |
| :-- | :-- |
| `MICROSOFT_TENANT_ID` | Your Entra ID directory (tenant) ID |
| `MICROSOFT_GATEWAY_CLIENT_ID` | Gateway app (middle tier). Callers authenticate against it; the gateway uses its secret for the OBO exchange |
| `MICROSOFT_GATEWAY_CLIENT_SECRET` | Gateway app client secret |
| `MICROSOFT_RUNTIME_CLIENT_ID` | Runtime app (downstream resource). The runtime validates `api://<runtime-client-id>` as the token audience |

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Register two applications in Microsoft Entra ID

#### 1.1 Runtime app (downstream resource)

1. Go to [entra.microsoft.com](https://entra.microsoft.com) -> **App registrations** -> **New registration**
2. Name: `A2A-Runtime-Resource`, Single tenant, **Register**
3. From **Overview**, copy **Application (client) ID** -> `MICROSOFT_RUNTIME_CLIENT_ID` and **Directory (tenant) ID** -> `MICROSOFT_TENANT_ID`
4. **Expose an API** -> set **Application ID URI** (accept the default `api://<runtime-client-id>`) -> **+ Add a scope** named `access_as_user` (consent: Admins and users, Enabled). This delegated scope is what the gateway app requests in step 1.2.6.

#### 1.2 Gateway app (middle tier)

1. **App registrations** -> **New registration**, name `A2A-Gateway-MiddleTier`, Single tenant, **Register**
2. From **Overview**, copy **Application (client) ID** -> `MICROSOFT_GATEWAY_CLIENT_ID`
3. **Certificates & secrets** -> **+ New client secret**. Copy the **Value** -> `MICROSOFT_GATEWAY_CLIENT_SECRET`
4. **Expose an API** -> **+ Add a scope** named `access_as_user` (consent: Admins and users, Enabled)
5. **Authentication** -> **+ Add a platform** -> **Web** -> redirect URI `http://localhost:9090/oauth2/callback` -> **Configure**
6. **API permissions** -> **+ Add a permission** -> **My APIs** tab (not "APIs my organization uses") -> select `A2A-Runtime-Resource` -> **Delegated permissions** -> add the `access_as_user` scope exposed in step 1.1.4
7. **API permissions** -> **Grant admin consent for [tenant]** -> **Yes** (all permissions should show green checkmarks)

> [!NOTE]
> By default, Entra ID issues **v1.0** access tokens (issuer `https://sts.windows.net/{tenant}/`). The gateway and runtime inbound discovery URLs in this tutorial use the v1.0 endpoint to match, while the OBO credential provider uses the v2.0 endpoint for the exchange.

Export the values you recorded:

```bash
export MICROSOFT_TENANT_ID=""             # Directory (tenant) ID
export MICROSOFT_GATEWAY_CLIENT_ID=""     # Gateway app (client) ID
export MICROSOFT_GATEWAY_CLIENT_SECRET="" # Gateway app client secret
export MICROSOFT_RUNTIME_CLIENT_ID=""     # Runtime app (client) ID

export ENTRA_DISCOVERY_URL="https://login.microsoftonline.com/$MICROSOFT_TENANT_ID/.well-known/openid-configuration"
```

### Step 2: Register the A2A agent (AgentCore CLI)

The A2A agent code is at [`gatewaylabproject/app/monitoring_a2a_agent/`](../../../../../gatewaylabproject/app/monitoring_a2a_agent/). It serves an A2A agent card and `message/send` handler backed by local CloudWatch tools.

Register the runtime with Entra ID inbound auth, validating the runtime app audience. A2A agents are Strands based, so `--framework Strands --model-provider Bedrock` are required.

```bash
agentcore add agent \
  --name monitoring_a2a_agent \
  --type byo \
  --build CodeZip \
  --language Python \
  --framework Strands \
  --model-provider Bedrock \
  --protocol A2A \
  --code-location app/monitoring_a2a_agent \
  --entrypoint main.py \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_RUNTIME_CLIENT_ID"
```

### Step 3: Deploy the A2A agent (AgentCore CLI)

```bash
agentcore deploy
```

Capture the runtime ARN and URL:

```bash
export RUNTIME_ARN=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'monitoring_a2a_agent'))
")
export RUNTIME_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'monitoring_a2a_agent'))
")

echo "Runtime ARN: $RUNTIME_ARN"
echo "Runtime URL: $RUNTIME_URL"
```

### Step 4: Test the Agent on [a2a-inspector](https://github.com/a2aproject/a2a-inspector)

#### Grant the runtime CloudWatch read permissions

The agent's tools call CloudWatch Logs and Metrics directly, so its runtime
execution role needs read access. Find the role from `agentcore status`, then
attach a read-only policy:

```bash
export MONITOR_ROLE_NAME=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
arn = data['deployedState']['targets']['default']['resources']['runtimes']['monitoring_a2a_agent']['roleArn']
print(arn.split('/')[-1])
")

aws iam put-role-policy \
  --role-name $MONITOR_ROLE_NAME \
  --policy-name MonitoringAgentCloudWatchRead \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "CloudWatchReadOnly",
        "Effect": "Allow",
        "Action": [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:FilterLogEvents",
          "logs:GetLogEvents",
          "cloudwatch:ListMetrics",
          "cloudwatch:GetMetricStatistics",
          "cloudwatch:GetMetricData",
          "cloudwatch:ListDashboards"
        ],
        "Resource": "*"
      }
    ]
  }'
```

#### Get an Entra ID user token

The runtime's inbound auth validates the runtime app audience, so acquire a user
token for that resource. The OBO lab's callback server handles the browser sign
in and captures the token. You sign in against the gateway app (which holds the
delegated permission to the runtime's `access_as_user` scope from step 1.2.6),
but pass `--scope` so the token is issued for the **runtime** audience
(`api://<runtime-client-id>`), which is what the runtime validates on inbound.
From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET \
  --scope "api://$MICROSOFT_RUNTIME_CLIENT_ID/access_as_user openid profile email"
```

Sign in when the browser opens, then read the captured token:

```bash
export BEARER_TOKEN=$(curl -sS http://localhost:9090/token \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")

echo "Bearer token: $BEARER_TOKEN"
```

#### Connect the inspector

Build the agent card URL from the runtime URL captured in Step 3:

```bash
export AGENT_CARD_URL="${RUNTIME_URL%/invocations}/invocations/.well-known/agent-card.json"

echo "Agent card URL: $AGENT_CARD_URL"
```

Start the [a2a-inspector](https://github.com/a2aproject/a2a-inspector) by following its README. In the UI, paste the `AGENT_CARD_URL` into the **Agent Card URL** field. Then, under **Custom Headers**, add the following two headers (AgentCore Runtime requires both):

| Header | Value |
| :-- | :-- |
| `Authorization` | `Bearer <BEARER_TOKEN>` |
| `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | a session id of 33 or more characters |

Click **Connect**. Once connected, the inspector shows the agent card and lets you send messages. Try:

> List up to 3 CloudWatch log group names.

![demo1](../images/cloudwatch-a2a.gif)

### Step 5: Create the gateway

HTTP targets attach to a gateway that has no protocol type set, so this step uses boto3. The script creates the gateway with Entra ID v1.0 inbound auth, validating the gateway app audience, and writes the gateway ID and URL to a script local `.env`.

> [!NOTE]
> This gateway (`runtime-agents-gateway`) is shared with the [HTTP agent on AgentCore Runtime](../../http-agents/http-runtime-agents/) lab. If you already created it there, this script detects the existing gateway and reuses it instead of creating a new one.

```bash
uv run python scripts/a2a-runtime-target/deploy_gateway.py \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"
```

Capture the gateway ID and URL written by the script:

```bash
export GATEWAY_ID=$(grep GATEWAY_ID scripts/a2a-runtime-target/.env | cut -d= -f2)
export GATEWAY_URL=$(grep GATEWAY_URL scripts/a2a-runtime-target/.env | cut -d= -f2)

echo "Gateway ID:  $GATEWAY_ID"
echo "Gateway URL: $GATEWAY_URL"
```

### Step 6: Create the OBO credential provider

The gateway exchanges the caller's inbound Entra token for a token scoped to the runtime, on behalf of the user. Create an OBO credential provider backed by the gateway app credentials.

```bash
uv run python scripts/a2a-runtime-target/deploy_credential.py \
  --name a2a-runtime-obo \
  --tenant-id $MICROSOFT_TENANT_ID \
  --client-id $MICROSOFT_GATEWAY_CLIENT_ID \
  --client-secret $MICROSOFT_GATEWAY_CLIENT_SECRET
```

The script calls `create_oauth2_credential_provider` with `credentialProviderVendor=CustomOauth2`, the Entra **v2.0** discovery URL, `clientAuthenticationMethod=CLIENT_SECRET_POST`, and `onBehalfOfTokenExchangeConfig` with `grantType=JWT_AUTHORIZATION_GRANT`. It writes the provider ARN to the script local `.env`:

```bash
export CREDENTIAL_PROVIDER_ARN=$(grep CREDENTIAL_PROVIDER_ARN scripts/a2a-runtime-target/.env | cut -d= -f2)

echo "Credential provider ARN: $CREDENTIAL_PROVIDER_ARN"
```

### Step 7: Create the HTTP runtime target

Attach the runtime to the gateway as an `http.agentcoreRuntime` target, using the OBO credential provider from Step 6. The outbound scope is the runtime resource (`api://<runtime-client-id>/.default`).

```bash
uv run python scripts/a2a-runtime-target/deploy_target.py \
  --gateway-id $GATEWAY_ID \
  --runtime-arn $RUNTIME_ARN \
  --credential-provider-arn $CREDENTIAL_PROVIDER_ARN \
  --scopes "api://$MICROSOFT_RUNTIME_CLIENT_ID/.default"
```

The script calls `create_gateway_target`. The `agentcoreRuntime` block points the target at the runtime ARN, and the credential provider configuration tells the gateway to perform the OBO exchange on every outbound request:

```json
{
  "targetConfiguration": {
    "http": {
      "agentcoreRuntime": {
        "arn": "<RUNTIME_ARN>",
        "qualifier": "DEFAULT"
      }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "OAUTH",
      "credentialProvider": {
        "oauthCredentialProvider": {
          "providerArn": "<CREDENTIAL_PROVIDER_ARN>",
          "scopes": ["api://<runtime-client-id>/.default"],
          "grantType": "TOKEN_EXCHANGE",
          "customParameters": { "requested_token_use": "on_behalf_of" }
        }
      }
    }
  ]
}
```

- `grantType: TOKEN_EXCHANGE` tells the gateway to swap the inbound token rather than fetch a fresh client credentials token.
- `customParameters.requested_token_use: on_behalf_of` is required for the Entra ID OBO endpoint.
- The provider ARN must point at a provider created with `onBehalfOfTokenExchangeConfig` (Step 6).

## Demo

Call the agent through the gateway with an **Entra ID user token scoped to the gateway app**. The gateway validates it, OBO exchanges it for a runtime scoped token, and forwards the A2A request. HTTP targets use path based routing of the form `{GATEWAY_URL}/{targetName}/invocations`.

> [!IMPORTANT]
> This step needs a **gateway-audience** token (`api://<gateway-client-id>`), which is different from the **runtime-audience** token Step 4 used for the direct inspector test. The gateway was created (Step 5) with `--allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"`, so a runtime-audience token is rejected here with `insufficient_scope`. Mint the gateway-audience token by running the callback server **without** `--scope` (the default requests the gateway app's own scope):
>
> ```bash
> lsof -ti :9090 | xargs kill 2>/dev/null
> uv run scripts/obo-token-exchange/token_callback_server.py \
>   $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET
>
> export BEARER_TOKEN="<PlaceBearerToken>"
> ```
>
> The gateway then performs the OBO exchange to the runtime audience internally; you never hand the gateway a runtime-audience token yourself.

```bash
export SESSION_ID=$(python3 -c "import uuid; print((uuid.uuid4().hex + uuid.uuid4().hex)[:40])")

curl -sS -X POST "${GATEWAY_URL}/a2a-runtime-target/invocations" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "List up to 3 CloudWatch log group names."}],
        "messageId": "m1"
      }
    }
  }'
```

The agent reads CloudWatch through its local tools and returns the result as an
A2A artifact.

You can also explore the agent interactively with the [a2a-inspector](https://github.com/a2aproject/a2a-inspector), as shown in [Step 4](#step-4-test-the-agent-on-a2a-inspector):

Fetch the agent card through the gateway. HTTP targets use the same path based routing, so the card sits under the target's `invocations` path. Use the **gateway-audience** `BEARER_TOKEN` from the demo above:

```bash
export GATEWAY_AGENT_CARD_URL="${GATEWAY_URL}/a2a-runtime-target/invocations/.well-known/agent-card.json"

echo "Gateway agent card URL: $GATEWAY_AGENT_CARD_URL"

curl -sS "$GATEWAY_AGENT_CARD_URL" \
  -H "Authorization: Bearer $BEARER_TOKEN" | python3 -m json.tool
```

The card describes the agent (name, version, capabilities, and skills). Its `url` field comes from the agent's `AGENTCORE_RUNTIME_URL` environment variable (see [`app/monitoring_a2a_agent/main.py`](../../../../../gatewaylabproject/app/monitoring_a2a_agent/main.py)) and defaults to the runtime invocation URL.

> [!IMPORTANT]
> An A2A client such as the [a2a-inspector](https://github.com/a2aproject/a2a-inspector) reads the card, then sends `message/send` to the card's `url` field. With the default value that URL is the **runtime** invocation URL, so the inspector calls the runtime directly and bypasses the gateway. That direct call requires a **runtime-audience** token (the Step 4 token), not the gateway-audience token, otherwise it returns `401 Unauthorized`. To make a card-following client route `message/send` back through the gateway (and the OBO exchange), set `AGENTCORE_RUNTIME_URL` to the gateway target URL `{GATEWAY_URL}/a2a-runtime-target/invocations/` when registering the agent in Step 2, so the card advertises the gateway path instead of the runtime URL. Fetching the card and calling the runtime directly (Step 4) continue to work either way.

![A2A monitoring agent answering a CloudWatch query](../images/cloudwatch-a2a.gif)

## Cleanup

Remove resources in reverse order. From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

Delete this lab's runtime target and OBO credential provider:

```bash
uv run python scripts/a2a-runtime-target/cleanup.py
```

> [!NOTE]
> The gateway is shared with the HTTP agent lab. Cleanup removes the shared gateway and its IAM role only when no targets remain on it. If the HTTP lab's target is still attached, the gateway is left in place.

Remove the CloudWatch policy added to the runtime role in Step 4:

```bash
aws iam delete-role-policy \
  --role-name $MONITOR_ROLE_NAME \
  --policy-name MonitoringAgentCloudWatchRead
```

Remove the A2A agent runtime:

```bash
agentcore remove agent --name monitoring_a2a_agent -y
agentcore deploy
```

Delete the two Entra ID app registrations (`A2A-Gateway-MiddleTier` and `A2A-Runtime-Resource`) from the [Entra portal](https://entra.microsoft.com) if no longer needed.

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [OBO Token Exchange](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
- [Gateway Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
