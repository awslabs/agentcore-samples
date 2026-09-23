# Create an AgentCore Gateway with Okta inbound auth

> [!NOTE]
> Everything Okta-specific here is condensed from
> [AgentCore Gateway Inbound Auth with Okta](../../../../../../02-set-up-inbound-authorization/mcp/okta/okta-gateway-inbound-auth.md),
> which goes deeper on MCP client wiring, multi-scope setups, and security considerations.
> Read that one if you want the full treatment; this one is the shortest path to a gateway
> the consent portal will accept.

Two things about how Okta issues tokens shape this whole setup:

- **The token audience is fixed by the Custom Authorization Server.** Okta's `/authorize`
  endpoint takes no `resource` (or `audience`) parameter — the `aud` claim is determined by
  *which* Custom Authorization Server the client calls. There is **nothing to register per
  gateway URL**.
- **Scopes are short names end to end.** An Okta custom scope such as `access_as_user` is the
  same short string the client requests, the string in the token's `scp` claim, and the string
  the gateway advertises. There is no short-vs-qualified split, so **no `advertisedScopeMapping`**
  is needed. See [One scope, one spelling](#one-scope-one-spelling).

## Prerequisites

- **[API Access Management](https://developer.okta.com/docs/concepts/api-access-management/)
  enabled on your Okta org.** Custom Authorization Servers are gated behind this paid add-on.
  Without it, `POST /api/v1/authorizationServers` fails and this guide cannot proceed — use the
  built-in `default` Custom Authorization Server instead (see the tip in Step 1) if your org
  has it, or enable API Access Management first.
- An **Okta API token** (SSWS) with permission to manage authorization servers and apps:
  *Security → API → Tokens → Create Token*.
- **AWS credentials** with permission to call `bedrock-agentcore-control` and to create an
  IAM role.
- **`uv`** and Python ≥ 3.12. Every command below runs from `authorization-code-flow/`
  — the parent of this directory.
- **boto3/botocore ≥ 1.43.88.** `pyproject.toml` floors both. Older SDKs do not know the
  consent portal operations you will need when you set up the portal.

> [!TIP]
> This guide uses `curl` against the
> [Okta Management API](https://developer.okta.com/docs/api/openapi/okta-management/guides/overview/)
> so every step is copy-pasteable. The same steps are available in the Admin Console
> (*Security → API → Authorization Servers*).

Set the two variables every Okta call below uses:

```bash
export OKTA_DOMAIN="your-org.okta.com"     # no scheme, no trailing slash
export OKTA_TOKEN="00abc...your-SSWS-token"
```

All Okta Management API calls send `Authorization: SSWS ${OKTA_TOKEN}`.

## Step 1: Create the Custom Authorization Server

This authorization server *represents your gateway as an API*. Tokens it issues are what the
gateway validates, and its `audiences` value becomes the `aud` claim in those tokens.

> [!TIP]
> Your org may already have a built-in Custom Authorization Server named **default**
> (`id = default`, audience `api://default`, discovery
> `https://<oktaDomain>/oauth2/default/.well-known/openid-configuration`). It ships with a
> scope-friendly access policy, so you can reuse it and **skip Step 3** — set `AS_ID=default`
> and audience `api://default`, and add only the `access_as_user` scope in Step 2.

> [!IMPORTANT]
> Do **not** use the **org** authorization server
> (`https://<oktaDomain>/.well-known/openid-configuration`, no `/oauth2/<id>/` segment). It is
> a different issuer and cannot host a custom `audience` or custom scopes, so `allowedScopes`
> validation against a custom scope will not work. The scripts refuse a discovery URL that is
> missing `/oauth2/` for exactly this reason.

```bash
AS_ID=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/authorizationServers" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "agentcore-gateway",
    "description": "AgentCore Gateway inbound auth",
    "audiences": ["api://agentcore-gateway"],
    "issuerMode": "ORG_URL"
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

echo "Authorization Server ID: $AS_ID"
```

## Step 2: Add the `access_as_user` scope

Define the scope clients request and the gateway validates. It appears in the access token's
**`scp`** claim as the short name `access_as_user`.

```bash
curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/scopes" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "access_as_user",
    "description": "Access the AgentCore Gateway as the signed-in user.",
    "consent": "IMPLICIT",
    "metadataPublish": "ALL_CLIENTS"
  }'
```

This `POST` is **additive** — it adds `access_as_user` without disturbing any existing scopes.

## Step 3: Add an access policy and rule

A Custom Authorization Server issues **no token** until at least one access policy has a rule
that matches the request. (The built-in `default` server already has one; if you reused it in
Step 1, skip to Step 4.)

```bash
POLICY_ID=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "OAUTH_AUTHORIZATION_POLICY",
    "status": "ACTIVE",
    "name": "AgentCore clients",
    "description": "Allow portal + MCP clients to obtain gateway tokens",
    "conditions": { "clients": { "include": ["ALL_CLIENTS"] } }
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies/${POLICY_ID}/rules" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "RESOURCE_ACCESS",
    "status": "ACTIVE",
    "name": "Default rule",
    "priority": 1,
    "conditions": {
      "people": { "users": { "include": [], "exclude": [] },
                  "groups": { "include": ["EVERYONE"], "exclude": [] } },
      "grantTypes": { "include": ["authorization_code"] },
      "scopes": { "include": ["access_as_user", "openid", "offline_access"] }
    }
  }'
```

> [!IMPORTANT]
> **List every scope the request carries, or `/authorize` fails `Policy evaluation failed`.**
> The consent portal always requests `openid` on top of your configured scopes, and MCP
> clients also request `offline_access` for a refresh token. The rule's `scopes.include` must
> contain **all** of them, or the whole request fails even though `access_as_user` is present.

> [!NOTE]
> `clients.include: ["ALL_CLIENTS"]` and `groups.include: ["EVERYONE"]` are convenient for a
> tutorial. For anything real, scope the policy to the specific client apps and the specific
> users/groups that should reach the gateway.

## Step 4: Export the values the scripts read

```bash
export OKTA_DISCOVERY_URL="https://${OKTA_DOMAIN}/oauth2/${AS_ID}/.well-known/openid-configuration"
export OKTA_AUDIENCE="api://agentcore-gateway"
export OKTA_ALLOWED_SCOPES="access_as_user"
```

| Variable | What it is |
| :--- | :--- |
| `OKTA_DISCOVERY_URL` | the Custom Authorization Server's OIDC discovery document. **Must** contain `/oauth2/<AS_ID>/`. This becomes the gateway's `discoveryUrl` and the portal's issuer. |
| `OKTA_AUDIENCE` | the authorization server's `audiences` value (`api://agentcore-gateway`). This becomes the gateway's `allowedAudience` and the portal's `idpConfig.audience`. **Not** the gateway URL — Okta stamps `aud` from the AS, and the gateway URL never appears in the token. |
| `OKTA_ALLOWED_SCOPES` | the **short** scope name. `openid` is prepended for you at portal-create time. |

> [!IMPORTANT]
> The `/oauth2/<AS_ID>/` segment is load-bearing. Pointing `OKTA_DISCOVERY_URL` at the org
> authorization server (no `/oauth2/<id>/`) advertises a different issuer and audience model,
> so validation against your custom scope fails with `insufficient_scope`. The scripts refuse
> to run if the URL is missing `/oauth2/` rather than letting you discover this later.

## Step 5: Create the gateway

```bash
uv run python scripts/deploy_gateway_entra.py --okta
```

The `--okta` flag names a profile in [`scripts/idps/`](../scripts/idps/). It is required and
has no default: a script that guessed its identity provider could create — or later delete —
the wrong one. (The script is provider-agnostic despite its name; the `--okta` flag is what
selects the Okta profile.)

The script creates an IAM role for the gateway, then calls
[`CreateGateway`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateGateway.html)
with `authorizerType: CUSTOM_JWT` and:

| Field | Value | Why |
| :--- | :--- | :--- |
| `discoveryUrl` | `$OKTA_DISCOVERY_URL` | where the gateway fetches signing keys and the expected issuer |
| `allowedAudience` | `[$OKTA_AUDIENCE]` | the AS `audiences` value, matched against the token's `aud` |
| `allowedScopes` | `["access_as_user"]` | the short scope, matched against the token's `scp` |

Note there is **no `advertisedScopeMapping`** — the next section is why.

### One scope, one spelling

Okta uses a single spelling of the scope on every side, which is what keeps this flow simple.

Okta issues the **short** name in the token: a token carrying `scp: ["access_as_user"]` is
what arrives at the gateway, so `allowedScopes` holds `access_as_user`. But Okta's `/authorize`
endpoint *also* accepts that exact short name — the client requests `access_as_user`, Okta
mints the token, and the gateway's protected-resource metadata advertises `access_as_user`.
Every side uses the same string.

Because the requested form and the token form are identical, `advertisedScopeMapping` would be
an identity map (`access_as_user → access_as_user`), which is a no-op. The gateway script
detects this from the profile's `scopeTemplate: "{scope}"` and **omits the mapping entirely**;
`deploy_gateway_entra.py --okta` therefore prints no "advertises … as …" line and sets no
mapping on the authorizer.

> [!IMPORTANT]
> Two things you might expect from an OAuth setup are **deliberately absent** here, and both
> absences are correct:
> - **No `advertisedScopeMapping`** on the gateway authorizer (short == qualified, above).
> - **No "register the gateway URL" step.** Okta's `/authorize` ignores the RFC 8707 `resource`
>   parameter an MCP client sends; the token audience is fixed by the Custom Authorization
>   Server. There is nothing to register per gateway URL, so `deploy_gateway_entra.py --okta`
>   prints no such command — it goes straight to pointing you at the consent portal.

The script also sets the protocol configuration every gateway in this directory uses: MCP
versions `2025-11-25`, `2025-06-18` and `2025-03-26`, `searchType: SEMANTIC`,
`streamingConfiguration.enableResponseStreaming: true`, and a one-hour session timeout.
`2025-11-25` is what enables URL-mode elicitation; the older two are there so a client that
cannot speak it still connects. The value lives in
`scripts/mcp_config.mcp_protocol_configuration()` and is shared across every gateway here so
they cannot drift.

When it finishes, `GATEWAY_ID` and `GATEWAY_URL` are in `scripts/.env`; every later script
reads them from there.

> [!NOTE]
> `scripts/.env` is a single flat namespace. `GATEWAY_ID` written here overwrites the one
> written by any other gateway deploy in this directory, so run one flow at a time rather than
> several at once.

## Step 6: (Optional) Register a public MCP-client app

The consent portal does **not** need this — it signs users in through its own confidential
client (see [`consent-portal.md`](consent-portal.md)). Do this only if you also want to point
Claude Code, Kiro, or another MCP client straight at the gateway.

```bash
CLIENT_APP_ID=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/apps" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "oidc_client",
    "label": "agentcore-gateway-mcp-client",
    "signOnMode": "OPENID_CONNECT",
    "credentials": { "oauthClient": { "token_endpoint_auth_method": "none" } },
    "settings": { "oauthClient": {
      "application_type": "native",
      "grant_types": ["authorization_code", "refresh_token"],
      "response_types": ["code"],
      "redirect_uris": ["http://localhost:8080/callback"]
    }}
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["credentials"]["oauthClient"]["client_id"])')

echo "Client App ID: $CLIENT_APP_ID"
```

`token_endpoint_auth_method: none` + `application_type: native` makes this a **public** client
that authenticates with PKCE, not a secret — the right shape for native/desktop MCP clients.

> [!IMPORTANT]
> **Assign your test users to this app.** Okta issues no token to a user who is not assigned,
> and the flow fails with `User is not assigned to the client application`. Assign every user
> who will use this client (in the Console: *Applications → your app → Assignments → Assign*).

Pin the callback port with `export MCP_OAUTH_CALLBACK_PORT=8080`, and see the
[inbound-auth guide](../../../../../../02-set-up-inbound-authorization/mcp/okta/okta-gateway-inbound-auth.md#step-5-register-a-client-app)
for the full client-wiring and port-pinning detail.

> [!NOTE]
> This public MCP-client app is **not** the portal login client. The portal needs a
> *confidential* web app with a secret; you create that separately in
> [`consent-portal.md`](consent-portal.md) Step 1.

## Step 7: Verify

The gateway serves its protected-resource metadata unauthenticated:

```bash
GATEWAY_URL=$(grep '^GATEWAY_URL=' scripts/.env | cut -d= -f2-)
curl -s "${GATEWAY_URL}/.well-known/oauth-protected-resource" | python3 -m json.tool
```

```json
{
    "authorization_servers": ["https://<OKTA_DOMAIN>/oauth2/<AS_ID>"],
    "resource": "https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
    "scopes_supported": ["access_as_user"]
}
```

`scopes_supported` showing the **short** `access_as_user` — the same string the client
requests and the same string that lands in the token's `scp` — is the one-spelling model
working. An unauthenticated call should be refused with the same hint:

```bash
curl -s -D - -o /dev/null -X POST "${GATEWAY_URL}/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | grep -iE '^HTTP/|www-authenticate'
```

```
HTTP/2 401
www-authenticate: Bearer error="invalid_token", scope="access_as_user", resource_metadata="https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/.well-known/oauth-protected-resource"
```

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `insufficient_scope` (403) although the token's `scp` matches `allowedScopes` | `discoveryUrl` points at the **org** authorization server or the wrong `AS_ID` | use `https://<domain>/oauth2/<AS_ID>/.well-known/openid-configuration` |
| `Policy evaluation failed` on `/authorize` | the access-policy rule is missing a requested scope — most often `openid` or `offline_access` | add every requested scope to the Step 3 rule |
| token `aud` not matching `allowedAudience` | `allowedAudience` is the gateway URL instead of the AS `audiences` value | set `OKTA_AUDIENCE` to the AS `audiences` string and recreate |
| `POST /authorizationServers` returns an error | API Access Management add-on not enabled | enable it, or reuse the built-in `default` server (Step 1 tip) |
| `OKTA_DISCOVERY_URL must contain /oauth2/` | the guard in `scripts/idp_config.py` fired | export the Custom Authorization Server's discovery URL, not the org one |

## Next

→ [Set up the consent portal](consent-portal.md)

## Cleanup

Only after the consent portal and any target have been cleaned up — the script refuses while
any target or portal still exists, since the portal's only source would be gone:

```bash
uv run python scripts/cleanup_gateway_entra.py --okta
```

It deletes the gateway and its IAM role. It leaves your Okta apps and the Custom Authorization
Server alone — the AS hosts the gateway's audience, the portal login client and the optional
MCP-client app are yours. Delete them yourself once you are done, e.g. (deactivate the app
first):

```bash
curl -X DELETE -H "Authorization: SSWS $OKTA_TOKEN" \
  "https://$OKTA_DOMAIN/api/v1/apps/<APP_ID>"
```
