# A2A Agent on Databricks Apps

Front an Agent-to-Agent (A2A) agent hosted on **Databricks Apps** through the gateway as an `http.passthrough` target with `protocolType=A2A`. The gateway validates the inbound caller with Microsoft Entra ID, then authenticates outbound to the Databricks App as a **Databricks service principal** (OAuth machine to machine) and forwards the A2A request.

This tutorial uses a currency-conversion agent (LangGraph + a Databricks-served LLM + a `get_exchange_rate` tool) served over A2A with the `a2a-sdk`, deployed to Databricks Apps.

## Architecture

![arch](../../images/agents.png)

| Component | Role |
| :-- | :-- |
| AgentCore Gateway | Fronts the Databricks App as an `http.passthrough` A2A target; validates the inbound Entra JWT and mints a Databricks token outbound |
| AgentCore Identity | Stores the Databricks OAuth credential provider the gateway uses outbound |
| Microsoft Entra ID | Issues the inbound JWT that authorizes the caller to the gateway |
| Databricks Apps | Hosts the A2A currency agent; enforces Databricks OAuth Bearer auth |

Path-based routing forwards `{GATEWAY_URL}/{targetName}/{path}` to the Databricks App URL.

> [!NOTE]
> **You do not need to federate your Databricks users with Entra ID.** Inbound auth (caller to gateway) uses Entra; outbound auth (gateway to the Databricks App) uses a Databricks **service principal** with OAuth client credentials. These are independent trust planes, and the service-principal path needs no SCIM user sync. You would only federate Databricks users with Entra (account-wide token federation) if you wanted the end user's identity to propagate into Databricks, which is out of scope for this proxy pattern.

## Tutorial details

| Item | Value |
| :-- | :-- |
| Target type | HTTP passthrough, `protocolType=A2A` |
| Endpoint | Your Databricks App URL |
| Inbound auth | Microsoft Entra ID (`CUSTOM_JWT`) |
| Outbound auth | Databricks service-principal OAuth (`CLIENT_CREDENTIALS`, scope `all-apis`) |
| Gateway | Shared `runtime-agents-gateway` (no protocol type) |
| Agent | Currency conversion (LangGraph) on Databricks Apps |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A Databricks workspace with Databricks Apps enabled, and a **service principal** with an OAuth secret
- A Microsoft Entra ID gateway app registration. This tutorial reuses the gateway from the [A2A agent](../agentcore-runtime/) and [HTTP agent](../../http-agents/http-runtime-agents/) labs; follow their Step 1 to register the gateway app and record `MICROSOFT_TENANT_ID` and `MICROSOFT_GATEWAY_CLIENT_ID`.

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Set up the currency agent on Databricks Apps

The agent code is at [`gatewaylabproject/app/databricks_currency_agent/`](../../../../../gatewaylabproject/app/databricks_currency_agent/): an `a2a-sdk` A2A server wrapping a LangGraph currency agent (`get_exchange_rate` over the Frankfurter API, backed by a Databricks-served LLM).

1. In your Databricks workspace, go to **Compute** -> **Apps** -> **Create app** -> **Custom**.
2. Point the app at the `app/databricks_currency_agent/` code (sync it into your workspace or a connected repo).
3. Point `MODEL_ID` at a Databricks model serving endpoint that exists in your workspace. The agent defaults to `databricks-claude-sonnet-4`; if that endpoint name is not served in your workspace the agent returns `ENDPOINT_NOT_FOUND` (note that workspace endpoint names may omit the `databricks-` prefix, e.g. `claude-sonnet-4`). List the endpoints available to you:

   ```bash
   databricks serving-endpoints list -o json \
     | python3 -c "import sys, json; [print(e['name']) for e in json.load(sys.stdin)]"
   ```

   Databricks Apps read environment variables from [`app.yaml`](../../../../../gatewaylabproject/app/databricks_currency_agent/app.yaml) (there is no separate environment-variable UI). Set `MODEL_ID` there to an endpoint from the list above:

   ```yaml
   command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]

   env:
     - name: 'MODEL_ID'
       value: 'claude-sonnet-4'
   ```
4. Click **Deploy**, then open the app and copy its URL. Re-deploy whenever you change `app.yaml`.

```bash
export DATABRICKS_APP_URL="https://<app-name>-<id>.cloud.databricksapps.com"
```

> [!NOTE]
> The agent card's `url` defaults to a relative `/` (Databricks Apps serve behind a reverse proxy). That works for direct access, but A2A clients that read the card and follow its `url` to send messages (for example the [a2a-inspector](https://github.com/a2aproject/a2a-inspector)) need an **absolute** URL, otherwise the send fails with `Request URL is missing an 'http://' or 'https://' protocol`. To route those clients back through the gateway, set the `AGENT_CARD_URL` environment variable in `app.yaml` to the gateway target URL `{GATEWAY_URL}/databricks-a2a/` and re-deploy. The gateway URL is created in Step 3, so add this after that step:
>
> ```yaml
> env:
>   - name: 'MODEL_ID'
>     value: 'claude-sonnet-4'
>   - name: 'AGENT_CARD_URL'
>     value: 'https://<your-gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/databricks-a2a/'
> ```

> [!NOTE]
> The agent calls the LLM as the **app's own service principal** (Databricks injects its `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` into the app), not the gateway service principal from Step 2. That app service principal needs **Can Query** on the `MODEL_ID` serving endpoint, otherwise the agent returns `403 PERMISSION_DENIED`. Grant it under **Serving** -> open the endpoint -> **Permissions** -> add the app's service principal -> **Can Query**. You can find the app service principal's client ID in the app's "App authorization" panel (or in the `403` error body).

> [!NOTE]
> The bundled `requirements.txt` pins the `a2a-sdk`, `langchain`, `langgraph`, and `databricks-langchain` stack below their 1.0 releases. Those majors changed import paths (for example `a2a.server.apps` was removed), so an unpinned install crashes the app on startup. Keep the upper bounds when editing dependencies.

### Step 2: Export credentials

Where to find each value:

- `MICROSOFT_TENANT_ID` and `MICROSOFT_GATEWAY_CLIENT_ID` come from the gateway app registration in Entra ID (see the [A2A agent](../agentcore-runtime/) lab Step 1.2).
- `DATABRICKS_WORKSPACE_HOST` is your workspace URL without the scheme or trailing slash (the hostname in your browser address bar, for example `dbc-xxxxxxxx-xxxx.cloud.databricks.com`).
- `DATABRICKS_SP_CLIENT_ID` / `DATABRICKS_SP_CLIENT_SECRET` come from a **service principal you control** with an OAuth secret:
  1. **Settings** -> **Identity and access** -> **Service principals** -> **Add service principal** (for example `A2ACurrencyAgent`). Leave the default workspace entitlements (Workspace access on); admin access is not needed.
  2. Open that service principal -> **Secrets** (OAuth secrets) -> **Generate secret**. Copy the **Client ID** (the service principal's Application ID, a GUID) to `DATABRICKS_SP_CLIENT_ID`, and the generated **Secret** value (shown once) to `DATABRICKS_SP_CLIENT_SECRET`.
  3. Grant the service principal **CAN USE** on the App so its `client_credentials` token is accepted: open the App's **Overview** tab -> **Share** -> select the service principal -> permission level **CAN USE** -> **Add** -> **Save**. Without this, the App's OAuth proxy rejects the token and returns a `302` redirect to its login endpoint instead of invoking the agent.

  Verify the credentials are accepted before wiring them into the gateway (expect `HTTP 200` with an `access_token`):

  ```bash
  curl -sS -o /dev/null -w "HTTP %{http_code}\n" -X POST \
    "https://$DATABRICKS_WORKSPACE_HOST/oidc/v1/token" \
    -u "$DATABRICKS_SP_CLIENT_ID:$DATABRICKS_SP_CLIENT_SECRET" \
    -d "grant_type=client_credentials" -d "scope=all-apis"
  ```

> [!NOTE]
> Do not use the **OAuth2 App Client ID** shown under the app's "User authorization" panel, nor the app's auto-created service principal (whose secret you cannot read). Both lead to authentication failures. Use a service principal you created with its own OAuth secret, as above. This tutorial uses the service-principal (machine to machine) path.

```bash
export MICROSOFT_TENANT_ID=""               # Directory (tenant) ID
export MICROSOFT_GATEWAY_CLIENT_ID=""       # Gateway app (client) ID
export DATABRICKS_WORKSPACE_HOST=""         # e.g. dbc-xxxxxxxx-xxxx.cloud.databricks.com
export DATABRICKS_SP_CLIENT_ID=""           # Databricks service principal application ID
export DATABRICKS_SP_CLIENT_SECRET=""       # Databricks service principal OAuth secret

export ENTRA_DISCOVERY_URL="https://login.microsoftonline.com/$MICROSOFT_TENANT_ID/.well-known/openid-configuration"
```

### Step 3: Create or reuse the gateway

HTTP passthrough targets attach to a gateway that has no protocol type set. This script creates that gateway with Entra ID inbound auth, or reuses it if it already exists.

```bash
uv run python scripts/databricks-a2a-target/deploy_gateway.py \
  --discovery-url $ENTRA_DISCOVERY_URL \
  --allowed-audience "api://$MICROSOFT_GATEWAY_CLIENT_ID"
```

> [!NOTE]
> This gateway (`runtime-agents-gateway`) is shared with the other runtime-agent labs. If you already created it there, this script detects the existing gateway and reuses it.

Capture the gateway URL:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/databricks-a2a-target/.env | cut -d= -f2)

echo "Gateway URL: $GATEWAY_URL"
```

### Step 4: Create the Databricks OAuth credential provider

The gateway authenticates to the App as a Databricks service principal. This creates a CustomOauth2 credential provider pointed at the workspace OIDC metadata; the target uses `client_credentials` (scope `all-apis`) to mint a Databricks token.

```bash
uv run python scripts/databricks-a2a-target/deploy_credential.py \
  --name databricks-a2a-oauth \
  --workspace-host $DATABRICKS_WORKSPACE_HOST \
  --client-id $DATABRICKS_SP_CLIENT_ID \
  --client-secret $DATABRICKS_SP_CLIENT_SECRET
```

Capture the provider ARN:

```bash
export CREDENTIAL_PROVIDER_ARN=$(grep CREDENTIAL_PROVIDER_ARN scripts/databricks-a2a-target/.env | cut -d= -f2)

echo "Credential provider ARN: $CREDENTIAL_PROVIDER_ARN"
```

### Step 5: Create the A2A passthrough target

Attach the Databricks App as a passthrough target with `protocolType=A2A` and the Databricks OAuth provider for outbound auth.

```bash
uv run python scripts/databricks-a2a-target/deploy_target.py \
  --endpoint "$DATABRICKS_APP_URL"
```

The script calls `create_gateway_target` with this configuration:

```json
{
  "targetConfiguration": {
    "http": {
      "passthrough": {
        "endpoint": "https://<app-name>-<id>.cloud.databricksapps.com",
        "protocolType": "A2A"
      }
    }
  },
  "credentialProviderConfigurations": [
    {
      "credentialProviderType": "OAUTH",
      "credentialProvider": {
        "oauthCredentialProvider": {
          "providerArn": "<CREDENTIAL_PROVIDER_ARN>",
          "scopes": ["all-apis"],
          "grantType": "CLIENT_CREDENTIALS"
        }
      }
    }
  ]
}
```

- `protocolType: A2A` gets a default schema, so no `schema` is needed (unlike `CUSTOM`).
- `grantType: CLIENT_CREDENTIALS` mints a Databricks service-principal token outbound. HTTP passthrough targets support `OAUTH` (not `API_KEY`), so this is the correct outbound type for Databricks OAuth.

## Demo

Call the agent through the gateway with your Entra ID token. Acquire one by reusing the OBO lab's callback server (from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory):

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET

export BEARER_TOKEN="<PlaceBearerToken>"
```

Send an A2A `message/send` through the gateway. The gateway validates the Entra JWT, mints a Databricks token, and forwards the request:

```bash
curl -sS -X POST "${GATEWAY_URL}/databricks-a2a/" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Convert 100 USD to EUR."}],
        "messageId": "m1"
      }
    }
  }'
```

The currency agent returns the conversion as an A2A artifact.

Fetch the agent card through the gateway. HTTP passthrough targets use path based routing, so the card sits under the target path:

```bash
export GATEWAY_AGENT_CARD_URL="${GATEWAY_URL}/databricks-a2a/.well-known/agent-card.json"

echo "Gateway agent card URL: $GATEWAY_AGENT_CARD_URL"

curl -sS "$GATEWAY_AGENT_CARD_URL" \
  -H "Authorization: Bearer $BEARER_TOKEN" | python3 -m json.tool
```

The card describes the agent (name, version, capabilities, and skills). Its `url` is whatever you set via `AGENT_CARD_URL` (Step 1); leave it relative (`/`) only if no card-following client needs it.

### Explore with the a2a-inspector

You can also drive the agent from the [a2a-inspector](https://github.com/a2aproject/a2a-inspector). Start it per its README, then in the UI set the **Agent Card URL** to:

```
${GATEWAY_URL}/databricks-a2a/.well-known/agent-card.json
```

Under **Custom Headers**, add `Authorization: Bearer <BEARER_TOKEN>` (the gateway-audience token from above). The inspector fetches the card, then sends `message/send` to the card's `url`.

> [!IMPORTANT]
> The inspector follows the card's `url` field to send messages, and it requires an **absolute** URL. With the default relative `url="/"` the send fails with `Request URL is missing an 'http://' or 'https://' protocol`. Set `AGENT_CARD_URL` in `app.yaml` to the gateway target URL (`{GATEWAY_URL}/databricks-a2a/`) and re-deploy (see Step 1), so the inspector routes `message/send` back through the gateway.

![Currency agent answering a conversion query through the gateway](../images/databricks-a2a.gif)

## Cleanup

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory:

```bash
uv run python scripts/databricks-a2a-target/cleanup.py
```

> [!NOTE]
> The gateway is shared with the other runtime-agent labs. Cleanup removes only this lab's `databricks-a2a` target and its Databricks OAuth credential provider. It deletes the shared gateway and its IAM role only when no targets remain. Delete the Databricks App and service principal from the Databricks console.

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [Deploy A2A protocol on Databricks Apps](https://community.databricks.com/t5/technical-blog/how-to-deploy-agent-to-agent-a2a-protocol-on-databricks-apps-gt/ba-p/134213)
- [Databricks OAuth machine-to-machine authentication](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-m2m)
