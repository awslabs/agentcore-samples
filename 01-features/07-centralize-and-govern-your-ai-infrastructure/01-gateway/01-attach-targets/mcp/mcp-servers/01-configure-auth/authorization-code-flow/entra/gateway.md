# Create an AgentCore Gateway with Microsoft Entra ID inbound auth

> [!NOTE]
> Everything Entra-specific here is condensed from
> [AgentCore Gateway Inbound Auth with Microsoft Entra ID](../../../../../../02-set-up-inbound-authorization/mcp/entraid/entra-id-gateway-inbound-auth.md),
> which goes deeper on MCP client wiring, multi-scope setups, and security considerations.
> Read that one if you want the full treatment; this one is the shortest path to a gateway
> the consent portal will accept.

## Prerequisites

- **Azure CLI** (`az`), logged in, with permission to create and update App Registrations
  in your Entra tenant. Confirm your tenant with `az account show --query tenantId -o tsv`.
- **AWS credentials** with permission to call `bedrock-agentcore-control` and to create an
  IAM role.
- **`uv`** and Python ≥ 3.12. Every command below runs from `authorization-code-flow/`
  — the parent of this directory.
- **boto3/botocore ≥ 1.43.88.** `pyproject.toml` floors both. Older SDKs do not know the
  consent portal operations you will need when you set up the portal.

## Step 1: Create the Entra resource app

This App Registration *represents your gateway as an API*. The tokens the gateway validates
are tokens issued **for this app**.

```bash
APP_ID=$(az ad app create \
  --display-name "agentcore-consent-portal-gateway" \
  --sign-in-audience "AzureADMyOrg" \
  --query appId -o tsv)

az ad sp create --id "$APP_ID"

echo "Resource app (client) ID: $APP_ID"
```

The service principal is not optional — without it the app cannot be used for token
issuance.

> [!NOTE]
> This same app will **also** serve as the consent portal's login client — do not create a
> second app for that later. Under Entra the portal must sign users in through the gateway's
> resource app or consent binds under a `sub` the gateway never looks up; see
> [`consent-portal.md` Step 1](consent-portal.md#step-1-prepare-the-resource-app-as-the-portals-login-client).

## Step 2: Expose the `access_as_user` scope

The scope you expose here is what lands in a token's `scp` claim and what the gateway
matches against. The same PATCH sets `requestedAccessTokenVersion: 2`, which Step 4 needs
in order to register an `https://` identifier URI on a domain you do not own.

```bash
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{
    \"identifierUris\": [\"api://$APP_ID\"],
    \"api\": {
      \"requestedAccessTokenVersion\": 2,
      \"oauth2PermissionScopes\": [
        {
          \"id\": \"$SCOPE_ID\",
          \"adminConsentDescription\": \"Access the AgentCore Gateway as the signed-in user.\",
          \"adminConsentDisplayName\": \"Access AgentCore Gateway\",
          \"isEnabled\": true,
          \"type\": \"User\",
          \"userConsentDescription\": \"Access the AgentCore Gateway on your behalf.\",
          \"userConsentDisplayName\": \"Access AgentCore Gateway\",
          \"value\": \"access_as_user\"
        }
      ]
    }
  }"

az ad app show --id "$APP_ID" --query 'api.requestedAccessTokenVersion' -o tsv
# expect: 2
```

> [!WARNING]
> Both arrays in that PATCH are **replaced wholesale**. If this app already exposes scopes
> or carries identifier URIs, include the existing entries in the body or you will remove
> them and break their clients. Likewise, flipping `requestedAccessTokenVersion` from
> `null`/`1` to `2` on an app that already has production clients changes the `aud` claim
> format and breaks their token validation — only do this on a new app.

## Step 3: Export the values the scripts read

```bash
export ENTRA_TENANT_ID=$(az account show --query tenantId -o tsv)
export ENTRA_RESOURCE="$APP_ID"
export ENTRA_DISCOVERY_URL="https://login.microsoftonline.com/${ENTRA_TENANT_ID}/v2.0/.well-known/openid-configuration"
```

| Variable | What it is |
| :--- | :--- |
| `ENTRA_TENANT_ID` | your tenant; only used to build the discovery URL |
| `ENTRA_RESOURCE` | the resource app's **GUID**. This becomes the gateway's `allowedAudience`. Not `api://<GUID>` — with v2.0 tokens Entra sets `aud` to the bare application id. |
| `ENTRA_DISCOVERY_URL` | the OIDC discovery document. **Must** contain `/v2.0/`. |

> [!IMPORTANT]
> The `/v2.0/` segment is load-bearing, and getting it wrong produces a failure that looks
> like a scope bug. The v1.0 document advertises `iss: https://sts.windows.net/{tenant}/`,
> which does not match the `iss` in the v2.0 tokens Entra actually issues, so validation
> fails with `insufficient_scope` even when the token's `scp` is exactly right. The scripts
> refuse to run if the URL is missing `/v2.0/` rather than letting you discover this later.

## Step 4: Create the gateway

```bash
uv run python scripts/deploy_gateway_entra.py --entra
```

The `--entra` flag names a profile in [`scripts/idps/`](../scripts/idps/). It is required
and has no default: a script that guessed its identity provider could create — or later
delete — the wrong one. Adding Okta means adding `scripts/idps/okta.json`, and `--okta`
appears on every one of these scripts on its own.

The script creates an IAM role for the gateway, then calls
[`CreateGateway`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateGateway.html)
with `authorizerType: CUSTOM_JWT` and:

| Field | Value | Why |
| :--- | :--- | :--- |
| `discoveryUrl` | `$ENTRA_DISCOVERY_URL` | where the gateway fetches signing keys and the expected issuer |
| `allowedAudience` | `[$ENTRA_RESOURCE]` | the app GUID, matched against the token's `aud` |
| `allowedScopes` | `["access_as_user"]` | the **short** scope, matched against the token's `scp` |
| `advertisedScopeMapping` | `{"access_as_user": "api://$ENTRA_RESOURCE/access_as_user"}` | the **qualified** scope, advertised to MCP clients |

### One scope, two spellings

This is the part worth slowing down for, because it is the same permission written two
different ways and the two are not interchangeable.

Entra puts the **short** name in the token. A token carrying
`scp: "access_as_user"` is what arrives at the gateway, so `allowedScopes` must hold the
short form — that is a string comparison against the claim.

But Entra's `/authorize` endpoint only accepts the **fully qualified**
`api://<APP_ID>/access_as_user`. Ask it for the short name and it does not know what you
mean. So anything requesting a token has to send the long form.

`advertisedScopeMapping` bridges those two facts, but only for one kind of caller: MCP
clients that read the gateway's
[RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) protected-resource metadata at
`/.well-known/oauth-protected-resource`, or the `scope=` hint in a `401`'s
`WWW-Authenticate` header. Those clients see the qualified form and send it to Entra.

> [!IMPORTANT]
> The consent portal is **not** an MCP client. It never fetches that metadata document, so
> `advertisedScopeMapping` does nothing for it. That is why the consent portal setup has you
> configure the qualified scope on the portal explicitly — it is not a redundant restatement
> of what you just set here.

The script also sets the protocol configuration every gateway in this directory uses:
MCP versions `2025-11-25`, `2025-06-18` and `2025-03-26`, `searchType: SEMANTIC`,
`streamingConfiguration.enableResponseStreaming: true`, and a one-hour session timeout.
`2025-11-25` is what enables URL-mode elicitation, which is how a tool invocation asks a
user to authorize; the older two are there so a client that cannot speak it still connects
instead of failing version negotiation. The value lives in
`scripts/mcp_config.mcp_protocol_configuration()` and is shared with the Cognito gateway in
[`../github/README.md`](../github/README.md) so the two cannot drift.

When it finishes, `GATEWAY_ID` and `GATEWAY_URL` are in `scripts/.env`; every later script
reads them from there.

> [!NOTE]
> `scripts/.env` is a single flat namespace. `GATEWAY_ID` written here overwrites the one
> written by `scripts/deploy_gateway.py --github`, so run one flow at a time rather than
> both at once.

## Step 5: Register the gateway URL as an identifier URI

MCP clients send the gateway URL as the [RFC 8707](https://datatracker.ietf.org/doc/html/rfc8707)
`resource` parameter on `/authorize`, and Entra rejects the call with `AADSTS9010010` until
that exact URL is registered on the resource app. `deploy_gateway_entra.py` prints these
commands with your values already filled in:

```bash
OBJECT_ID=$(az ad app show --id "$ENTRA_RESOURCE" --query id -o tsv)
GATEWAY_URL=$(grep '^GATEWAY_URL=' scripts/.env | cut -d= -f2-)

az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{\"identifierUris\":[\"api://$ENTRA_RESOURCE\",\"$GATEWAY_URL/mcp\"]}"
```

> [!TIP]
> The consent portal does **not** need this. It authenticates users through its own client
> app and never sends a `resource` parameter. Skip this step if you only ever reach your
> tools through the portal; do it if you also want to point Claude Code, Kiro, or another
> MCP client straight at the gateway.

Note the `identifierUris` array is replaced wholesale here too, which is why
`api://$ENTRA_RESOURCE` is repeated — dropping it would break the scope you exposed in
Step 2.

## Step 6: Verify

The gateway serves its protected-resource metadata unauthenticated:

```bash
GATEWAY_URL=$(grep '^GATEWAY_URL=' scripts/.env | cut -d= -f2-)
curl -s "${GATEWAY_URL}/.well-known/oauth-protected-resource" | python3 -m json.tool
```

```json
{
    "authorization_servers": ["https://login.microsoftonline.com/<TENANT_ID>/v2.0"],
    "resource": "https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
    "scopes_supported": ["api://<APP_ID>/access_as_user"]
}
```

`scopes_supported` showing the **qualified** form is `advertisedScopeMapping` working. An
unauthenticated call should be refused with the same hint:

```bash
curl -s -D - -o /dev/null -X POST "${GATEWAY_URL}/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | grep -iE '^HTTP/|www-authenticate'
```

```
HTTP/2 401
www-authenticate: Bearer error="invalid_token", scope="api://<APP_ID>/access_as_user", resource_metadata="https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/.well-known/oauth-protected-resource"
```

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `insufficient_scope` (403) although the token's `scp` matches `allowedScopes` | `discoveryUrl` is the v1.0 endpoint, so the advertised issuer does not match the token's `iss` | put `/v2.0/` in the path |
| `AADSTS9010010` on `/authorize` | the gateway URL is not an identifier URI on the resource app | Step 5 |
| `AADSTS500011` — resource principal not found | `az ad sp create --id "$APP_ID"` was skipped | run it |
| `invalid_token` (401) with a token that looks correct | `allowedAudience` holds `api://<GUID>` instead of the bare GUID | recreate the gateway with the GUID |
| `ENTRA_DISCOVERY_URL must contain /v2.0/` | the guard in `scripts/idp_config.py` fired | export the v2.0 URL |

## Next

→ [Set up the consent portal](consent-portal.md)

## Cleanup

Only after the consent portal and the GitHub target have been cleaned up — the script
refuses while any target or portal still exists, since the portal's only source would be
gone:

```bash
uv run python scripts/cleanup_gateway_entra.py --entra
```

It deletes the gateway and its IAM role. It leaves the Entra resource app alone: it is the
gateway's audience, the portal's `audience`, and potentially other clients' scope. Delete
it yourself with `az ad app delete --id "$ENTRA_RESOURCE"` if you are done with it.