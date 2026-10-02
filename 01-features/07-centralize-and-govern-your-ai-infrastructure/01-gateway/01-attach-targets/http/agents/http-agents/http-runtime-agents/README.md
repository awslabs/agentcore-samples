# HTTP Agent on AgentCore Runtime with Entra ID OBO

Attach an HTTP agent hosted on Amazon Bedrock AgentCore Runtime to your gateway as an `http.agentcoreRuntime` target, with **Microsoft Entra ID** for inbound auth and **OBO (on-behalf-of) token exchange** for outbound auth to the runtime.

A caller signs in with Entra ID and sends the gateway an app scoped access token. The gateway validates it, then exchanges it on behalf of the user for a token scoped to the runtime, and forwards the request. The user's identity is preserved end to end, from caller through gateway to runtime.

This tutorial uses an AWS Documentation assistant (a Strands HTTP agent) that answers questions about AWS services using the AWS Documentation MCP server.

## Architecture

![arch](../../images/agents.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts the runtime agent as an HTTP target with path based routing; validates the inbound Entra token and performs the OBO exchange |
| AgentCore Runtime | Hosts the AWS docs HTTP agent (protocol `HTTP`); validates the OBO exchanged token on inbound |
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
2. Name: `HTTP-Runtime-Resource`, Single tenant, **Register**
3. From **Overview**, copy **Application (client) ID** -> `MICROSOFT_RUNTIME_CLIENT_ID` and **Directory (tenant) ID** -> `MICROSOFT_TENANT_ID`
4. **Expose an API** -> set **Application ID URI** (accept the default `api://<runtime-client-id>`) -> **+ Add a scope** named `access_as_user` (consent: Admins and users, Enabled). This delegated scope is what the gateway app requests in step 1.2.6.

#### 1.2 Gateway app (middle tier)

1. **App registrations** -> **New registration**, name `HTTP-Gateway-MiddleTier`, Single tenant, **Register**
2. From **Overview**, copy **Application (client) ID** -> `MICROSOFT_GATEWAY_CLIENT_ID`
3. **Certificates & secrets** -> **+ New client secret**. Copy the **Value** -> `MICROSOFT_GATEWAY_CLIENT_SECRET`
4. **Expose an API** -> **+ Add a scope** named `access_as_user` (consent: Admins and users, Enabled)
5. **Authentication** -> **+ Add a platform** -> **Web** -> redirect URI `http://localhost:9090/oauth2/callback` -> **Configure**
6. **API permissions** -> **+ Add a permission** -> **My APIs** tab (not "APIs my organization uses") -> select `HTTP-Runtime-Resource` -> **Delegated permissions** -> add the `access_as_user` scope exposed in step 1.1.4
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

### Step 2: Register the HTTP agent (AgentCore CLI)

The HTTP agent code is at [`gatewaylabproject/app/aws_docs_agent/`](../../../../../gatewaylabproject/app/aws_docs_agent/). It implements the AgentCore Runtime HTTP contract: it reads a `prompt` from the invocation payload and returns a `result`.

Register the runtime with Entra ID inbound auth, validating the runtime app audience. The agent is Strands based, so `--framework Strands --model-provider Bedrock` are required.

```bash
agentcore add agent \
  --name aws_docs_http \
  --type byo \
  --build CodeZip \
  --language Python \
  --framework Strands \
  --model-provider Bedrock \
  --protocol HTTP \
  --code-location app/aws_docs_agent \
  --entrypoint main.py \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_RUNTIME_CLIENT_ID"
```

### Step 3: Deploy the HTTP agent (AgentCore CLI)

```bash
agentcore deploy
```

Capture the runtime ARN and URL:

```bash
export RUNTIME_ARN=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'aws_docs_http'))
")
export RUNTIME_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'aws_docs_http'))
")

echo "Runtime ARN: $RUNTIME_ARN"
echo "Runtime URL: $RUNTIME_URL"
```

### Step 4: Test the runtime directly

The runtime is registered with `CUSTOM_JWT` inbound auth (Step 2), so `agentcore invoke` needs a bearer token; it does not auto-fetch one. The runtime validates the **runtime** app audience, so mint a token for that resource. Sign in against the gateway app (which holds the delegated permission to the runtime's `access_as_user` scope from step 1.2.6), passing `--scope` so the token is issued for the runtime audience. From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET \
  --scope "api://$MICROSOFT_RUNTIME_CLIENT_ID/access_as_user openid profile email"
```

Sign in when the browser opens, then read the captured token and invoke the runtime with it:

```bash
export RUNTIME_TOKEN="<Put Bearer Token>"

agentcore invoke --runtime aws_docs_http \
  --bearer-token "$RUNTIME_TOKEN" \
  --json '{"prompt":"In one sentence, what is Amazon S3?"}'
```

The agent answers from the AWS documentation and returns a `result`.


### Step 5: Create the gateway

HTTP targets attach to a gateway that has no protocol type set, so this step uses boto3. The script creates the gateway with Entra ID v1.0 inbound auth, validating the gateway app audience, and writes the gateway ID and URL to a script local `.env`.

> [!NOTE]
> This gateway (`runtime-agents-gateway`) is shared with the [A2A agent on AgentCore Runtime](../../a2a-agents/agentcore-runtime/) lab. If you already created it there, this script detects the existing gateway and reuses it instead of creating a new one.

```bash
uv run python scripts/http-runtime-target/deploy_gateway.py \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"
```

Capture the gateway ID and URL written by the script:

```bash
export GATEWAY_ID=$(grep GATEWAY_ID scripts/http-runtime-target/.env | cut -d= -f2)
export GATEWAY_URL=$(grep GATEWAY_URL scripts/http-runtime-target/.env | cut -d= -f2)

echo "Gateway ID:  $GATEWAY_ID"
echo "Gateway URL: $GATEWAY_URL"
```

### Step 6: Create the OBO credential provider

The gateway exchanges the caller's inbound Entra token for a token scoped to the runtime, on behalf of the user. Create an OBO credential provider backed by the gateway app credentials.

```bash
uv run python scripts/http-runtime-target/deploy_credential.py \
  --name http-runtime-obo \
  --tenant-id $MICROSOFT_TENANT_ID \
  --client-id $MICROSOFT_GATEWAY_CLIENT_ID \
  --client-secret $MICROSOFT_GATEWAY_CLIENT_SECRET
```

The script calls `create_oauth2_credential_provider` with `credentialProviderVendor=CustomOauth2`, the Entra **v2.0** discovery URL, `clientAuthenticationMethod=CLIENT_SECRET_POST`, and `onBehalfOfTokenExchangeConfig` with `grantType=JWT_AUTHORIZATION_GRANT`. It writes the provider ARN to the script local `.env`:

```bash
export CREDENTIAL_PROVIDER_ARN=$(grep CREDENTIAL_PROVIDER_ARN scripts/http-runtime-target/.env | cut -d= -f2)

echo "Credential provider ARN: $CREDENTIAL_PROVIDER_ARN"
```

### Step 7: Create the HTTP runtime target

Attach the runtime to the gateway as an `http.agentcoreRuntime` target, using the OBO credential provider from Step 6. The outbound scope is the runtime resource (`api://<runtime-client-id>/.default`). The script ships an OpenAPI schema ([`scripts/http-runtime-target/agent-schema.yaml`](../../../../../gatewaylabproject/scripts/http-runtime-target/agent-schema.yaml)) and attaches it inline.

```bash
uv run python scripts/http-runtime-target/deploy_target.py \
  --gateway-id $GATEWAY_ID \
  --runtime-arn $RUNTIME_ARN \
  --credential-provider-arn $CREDENTIAL_PROVIDER_ARN \
  --scopes "api://$MICROSOFT_RUNTIME_CLIENT_ID/.default" \
  --schema-file scripts/http-runtime-target/agent-schema.yaml
```

The script calls `create_gateway_target`. The `agentcoreRuntime` block points the target at the runtime ARN and includes the schema; the credential provider configuration tells the gateway to perform the OBO exchange on every outbound request:

```json
{
  "targetConfiguration": {
    "http": {
      "agentcoreRuntime": {
        "arn": "<RUNTIME_ARN>",
        "qualifier": "DEFAULT",
        "schema": {
          "source": {
            "inlinePayload": "<openapi-schema-string>"
          }
        }
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

- `arn` (required) is the AgentCore Runtime agent ARN; `qualifier` (optional) defaults to `DEFAULT`.
- `schema` (optional in general, but **required for HTTP-protocol runtimes** to use policy-engine features such as guardrails). MCP and A2A runtimes get a default schema, so they do not need one. The format is auto detected as OpenAPI or Smithy.
- The schema `source` is either `inlinePayload` (the schema content as a string, used here) or `s3` (an S3 URI such as `s3://DOC-EXAMPLE-BUCKET/agent-schema.yaml`).
- `grantType: TOKEN_EXCHANGE` plus `customParameters.requested_token_use: on_behalf_of` performs the OBO exchange. The provider ARN must point at a provider created with `onBehalfOfTokenExchangeConfig` (Step 6).

Verify the target reaches `READY`:

```bash
agentcore status
```

## Demo

Next, acquire the **gateway-audience** token used for the gateway demo below. The gateway validates the gateway app audience (Step 5), so run the callback server **without** `--scope` (the default requests the gateway app's own scope):

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET

export BEARER_TOKEN="<Put Bearer Token>"

echo "Bearer token: $BEARER_TOKEN"
```

Call the agent through the gateway with your **Entra ID user token** from Step 4. The gateway validates it, OBO exchanges it for a runtime scoped token, and forwards the request. HTTP targets use path based routing of the form `{GATEWAY_URL}/{targetName}/invocations`.

```bash
export SESSION_ID=$(python3 -c "import uuid; print((uuid.uuid4().hex + uuid.uuid4().hex)[:40])")

curl -sS -X POST "${GATEWAY_URL}/http-runtime-target/invocations" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
  -d '{"prompt": "In one sentence, what is Amazon S3?"}'
```

The agent answers from the AWS documentation and returns a `result`.

![AWS docs HTTP agent answering through the gateway](../images/runtime.gif)

## Cleanup

Remove resources in reverse order. From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

Delete this lab's runtime target and OBO credential provider:

```bash
uv run python scripts/http-runtime-target/cleanup.py
```

> [!NOTE]
> The gateway is shared with the A2A agent lab. Cleanup removes the shared gateway and its IAM role only when no targets remain on it. If the A2A lab's target is still attached, the gateway is left in place.

Remove the HTTP agent runtime:

```bash
agentcore remove agent --name aws_docs_http -y
agentcore deploy
```

Delete the two Entra ID app registrations (`HTTP-Gateway-MiddleTier` and `HTTP-Runtime-Resource`) from the [Entra portal](https://entra.microsoft.com) if no longer needed.

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [OBO Token Exchange](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
- [Gateway Outbound Auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
