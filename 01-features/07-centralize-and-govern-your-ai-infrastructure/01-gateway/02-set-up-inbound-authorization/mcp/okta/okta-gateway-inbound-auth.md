# AgentCore Gateway Inbound Auth with Okta

![claude](./claude.gif)

![krio](./kiro.gif)

This guide walks through setting up a new AgentCore Gateway with `CUSTOM_JWT` inbound authorization backed by an **Okta Custom Authorization Server**. By the end, an MCP client (Claude Code, Codex, Kiro, etc.) can connect to your gateway, discover OAuth metadata via RFC 9728, acquire a token from Okta, and invoke MCP tools.

Two things about how Okta issues tokens shape the setup below:

- **The token audience is fixed by the Custom Authorization Server.** Okta's `/authorize` endpoint takes no `resource` (or `audience`) parameter — the `aud` claim is determined by *which* Custom Authorization Server the client calls. There is nothing to register per gateway URL.
- **Scopes are short names end to end.** An Okta custom scope such as `access_as_user` is the same short string the client requests, the string in the token's `scp` claim, and the string the gateway advertises — so the gateway validates and advertises it unchanged.

## Prerequisites

- **[API Access Management](https://developer.okta.com/docs/concepts/api-access-management/) enabled on your Okta org.** Custom Authorization Servers are gated behind this add-on. If your org does not have it, `POST /api/v1/authorizationServers` returns an error and this guide cannot proceed — use the built-in `default` Custom Authorization Server (see the tip in Step 1) if your org has it, or enable API Access Management first.
- An **Okta API token** (SSWS) with permission to manage authorization servers and apps: *Security → API → Tokens → Create Token*. Or an [OAuth 2.0 service app](https://developer.okta.com/docs/guides/implement-oauth-for-okta/main/) with scopes `okta.authorizationServers.manage` and `okta.apps.manage`.
- AWS CLI (`aws`) with permissions to call `bedrock-agentcore-control` (`CreateGateway`, `GetGateway`).
- A gateway service role ARN (an IAM role AgentCore Gateway can assume).

> [!TIP]
> This guide uses `curl` against the [Okta Management API](https://developer.okta.com/docs/api/openapi/okta-management/guides/overview/) so every step is copy-pasteable. You can perform the same Okta steps through the [Admin Console](https://developer.okta.com/docs/guides/customize-authz-server/main/) (*Security → API → Authorization Servers*).

Set the two variables every Okta call below uses:

```bash
export OKTA_DOMAIN="your-org.okta.com"     # no scheme, no trailing slash
export OKTA_TOKEN="00abc...your-SSWS-token"
```

All Okta Management API calls send `Authorization: SSWS ${OKTA_TOKEN}`.

## Overview

| Step | What happens |
|------|-------------|
| 1 | Create an Okta **Custom Authorization Server** (the issuer whose tokens the gateway validates), with an `audience`. |
| 2 | Add a custom scope (`access_as_user`) to that authorization server. |
| 3 | Add an **access policy and rule** — a Custom Authorization Server mints no token without one. |
| 4 | Create the AgentCore Gateway with `CUSTOM_JWT`, `discoveryUrl`, `allowedAudience`, and `allowedScopes`. |
| 5 | Register a **public (PKCE) client app** for your MCP clients. |
| 6 | Connect your MCP client to the gateway. |

There is **no** "register the gateway URL" step — the token audience comes from the Custom Authorization Server, as noted at the top.

## Step 0: Recommend reading the Security Considerations first

Read more [here](#security-considerations).

## Step 1: Create the Custom Authorization Server

> [!TIP]
> Your org may already have a built-in Custom Authorization Server named **default** (`id = default`, audience `api://default`, discovery `https://<oktaDomain>/oauth2/default/.well-known/openid-configuration`). It ships with a scope-friendly access policy, so you can reuse it and skip Step 3 — set `AS_ID=default` and audience `api://default`, and add only the `access_as_user` scope in Step 2.

> [!IMPORTANT]
> Do **not** use the **org** authorization server (`https://<oktaDomain>/.well-known/openid-configuration`, no `/oauth2/<id>/` segment). It is a different issuer and cannot host a custom `audience` or custom scopes, so `allowedScopes` validation against a custom scope will not work.

This authorization server represents your gateway API. Tokens it issues are what the gateway validates. Its `audiences` value becomes the `aud` claim in those tokens.

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

Derive the issuer and discovery URL (used in Step 4 and by MCP clients):

```bash
ISSUER="https://${OKTA_DOMAIN}/oauth2/${AS_ID}"
DISCOVERY_URL="${ISSUER}/.well-known/openid-configuration"

echo "Issuer:     $ISSUER"
echo "Discovery:  $DISCOVERY_URL"
echo "Audience:   api://agentcore-gateway"
```

Key fields to note:

| Field | Purpose |
|-------|---------|
| `id` | The authorization server ID. Appears in the issuer and discovery URL (`/oauth2/<id>/`). |
| `audiences` | The token `aud` claim value. Used as `allowedAudience` on the gateway. |
| `issuer` | `https://<oktaDomain>/oauth2/<id>`. What the gateway sees as the token `iss` and what the PRM advertises as the authorization server. |

## Step 2: Add the `access_as_user` scope

Define the scope clients request and the gateway validates. It appears in the access token's **`scp`** claim as the short name `access_as_user`.

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

The fully qualified scope string clients request is simply:

```
access_as_user
```

This is the whole reason Okta needs no `advertisedScopeMapping`: the client requests `access_as_user`, Okta issues a token with `scp: ["access_as_user"]`, and the gateway both validates and advertises `access_as_user`. Every side uses the same short form.

> [!NOTE]
> Scope creation here is **additive** — this `POST` adds `access_as_user` without disturbing any existing scopes on the authorization server.

### Multiple scopes

You can define more scopes for fine-grained access control (e.g. `mcp.github`, `mcp.web_search`) — one `POST .../scopes` call each. Every scope you want the gateway to validate must be listed in `allowedScopes` at gateway creation time (Step 4).

## Step 3: Add an access policy and rule

A Custom Authorization Server issues **no token** until at least one access policy has a rule that matches the request — "if no matching rule is found, the authorization request fails." (The built-in `default` server already has one; if you reused it in Step 1, skip to Step 4.)

Create the policy:

```bash
POLICY_ID=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "OAUTH_AUTHORIZATION_POLICY",
    "status": "ACTIVE",
    "name": "AgentCore clients",
    "description": "Allow MCP clients to obtain gateway tokens",
    "conditions": { "clients": { "include": ["ALL_CLIENTS"] } }
  }' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

echo "Policy ID: $POLICY_ID"
```

Add a rule that permits the authorization-code grant, the `access_as_user` scope, and your users:

```bash
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
> Without a matching policy **and** rule, Okta rejects the `/authorize` request even though the authorization server, the scope, and the client all exist. The pieces look present, but token issuance fails until a rule explicitly allowlists the request.

> [!IMPORTANT]
> **List every scope the client requests, or the request fails `Policy evaluation failed`.** MCP clients like Claude Code request `scope=access_as_user offline_access` (they need `offline_access` for a refresh token), plus `openid`. The rule's `scopes.include` must contain **all** of them — if `offline_access` is missing, the whole `/authorize` fails even though `access_as_user` is present. To widen an existing rule (e.g. the built-in `default` server's, or one you already created), fetch it, append the scope, and `PUT` it back:
> ```bash
> RULE_ID=$(curl -s "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies/${POLICY_ID}/rules" \
>   -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
>   | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')
>
> curl -s "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies/${POLICY_ID}/rules/${RULE_ID}" \
>   -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
> | python3 -c 'import sys,json; r=json.load(sys.stdin); sc=r["conditions"].setdefault("scopes",{}).setdefault("include",[]); sc.append("offline_access") if "offline_access" not in sc else None; json.dump(r,sys.stdout)' \
> | curl -s -X PUT "https://${OKTA_DOMAIN}/api/v1/authorizationServers/${AS_ID}/policies/${POLICY_ID}/rules/${RULE_ID}" \
>     -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
>     -H "Content-Type: application/json" -d @- \
> | python3 -c 'import sys,json; print("scopes now:", json.load(sys.stdin)["conditions"]["scopes"]["include"])'
> ```

> [!NOTE]
> `conditions.clients.include: ["ALL_CLIENTS"]` and `groups.include: ["EVERYONE"]` are convenient for a tutorial. For anything real, scope the policy to the specific client app (Step 5) and the specific users/groups that should reach the gateway — see [Security Considerations](#security-considerations).

## Step 4: Create the AgentCore Gateway

### 4a. Gather your Okta values

```bash
echo "Discovery:  $DISCOVERY_URL"           # https://<domain>/oauth2/<AS_ID>/.well-known/openid-configuration
echo "Audience:   api://agentcore-gateway"  # the authorization server's audiences value
```

> [!IMPORTANT]
> `allowedAudience` must be the authorization server's **`audiences`** value (`api://agentcore-gateway` here), **not** the gateway URL. Okta stamps the token `aud` from the authorization server's configured audience; the gateway URL never appears in the token.

### 4b. Create the gateway

The gateway's auto-generated PRM at `/.well-known/oauth-protected-resource` advertises `scopes_supported`, which by default is exactly `allowedScopes`. With Okta this is already correct: the client requests `access_as_user`, and the token carries `access_as_user` in `scp`. There is no short-vs-qualified mismatch to bridge, so **no `advertisedScopeMapping` is needed**.

```bash
aws bedrock-agentcore-control create-gateway \
  --region <REGION> \
  --name "my-okta-gateway" \
  --role-arn "arn:aws:iam::<ACCOUNT_ID>:role/<GATEWAY_SERVICE_ROLE>" \
  --protocol-type MCP \
  --authorizer-type CUSTOM_JWT \
  --exception-level DEBUG \
  --protocol-configuration '{
    "mcp": {
      "supportedVersions": ["2025-11-25", "2025-06-18", "2025-03-26"],
      "sessionConfiguration": { "sessionTimeoutInSeconds": 3600 },
      "streamingConfiguration": { "enableResponseStreaming": true }
    }
  }' \
  --authorizer-configuration "{
    \"customJWTAuthorizer\": {
      \"discoveryUrl\": \"$DISCOVERY_URL\",
      \"allowedAudience\": [\"api://agentcore-gateway\"],
      \"allowedScopes\": [\"access_as_user\"]
    }
  }"
```

Understanding the authorizer fields:

| Field | What it does |
|-------|-------------|
| `discoveryUrl` | Points at your Custom Authorization Server's OIDC configuration (`/oauth2/<AS_ID>/.well-known/openid-configuration`). The gateway fetches signing keys and issuer info from here. Must include the `/oauth2/<AS_ID>/` segment. |
| `allowedAudience` | Array of `aud` values the gateway accepts. Use the authorization server's `audiences` value (`api://agentcore-gateway`), not the gateway URL. |
| `allowedScopes` | Array of scope values validated against the token's `scp` claim. At least one must match. These also appear verbatim in the PRM `scopes_supported` (no mapping needed). |

> [!NOTE]
> `advertisedScopeMapping` still exists and is valid on Okta gateways — you would use it only if a broker or downstream authorization server put a *different* string in the token's `scp` than the one you want clients to request. With a single Okta Custom Authorization Server and short scopes, you do not need it.

Wait for the gateway to reach READY and save its URL:

```bash
GATEWAY_ID=<gateway-id-from-output>

aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" --region <REGION> \
  --query '{status:status, url:gatewayUrl}' --output json

GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" --region <REGION> \
  --query 'gatewayUrl' --output text)

echo "Gateway URL: $GATEWAY_URL"
```

> [!NOTE]
> There is intentionally no step here to register `$GATEWAY_URL/mcp` anywhere in Okta. Okta's `/authorize` does not consume the RFC 8707 `resource` parameter an MCP client sends (it is ignored, not rejected); the token audience is fixed by the Custom Authorization Server the client calls. The gateway still publishes `$GATEWAY_URL/mcp` as the PRM `resource` for client discovery — that is a gateway concern, not an Okta registration.

## Step 5: Register a client app

MCP clients need a `client_id`. Create a **public** (PKCE, no secret) OIDC app.

```bash
CLIENT_APP_ID=$(curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/apps" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "oidc_client",
    "label": "my-gateway-mcp-client",
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

> [!NOTE]
> `token_endpoint_auth_method: none` + `application_type: native` makes this a public client that authenticates with **PKCE**, not a secret — the right shape for native/desktop MCP clients (Claude Code, Codex, Kiro), which cannot store a secret. See [Security Considerations](#security-considerations).

### Redirect URIs for Claude Code

Claude Code redeems the OAuth code on a **`http://localhost` loopback callback**. Okta requires every redirect URI to be pre-registered and matches it **exactly, including the port**. By default Claude Code picks a **new random port on every sign-in attempt**, so registering one port and retrying just fails on the next port — you chase a moving target and see `The 'redirect_uri' parameter must be a Login redirect URI in the client app settings`.

**Pin the port instead.** Claude Code honors the `MCP_OAUTH_CALLBACK_PORT` environment variable — set it to the port you registered (the create body in Step 5 already registered `8080`) and every attempt uses that same callback:

```bash
export MCP_OAUTH_CALLBACK_PORT=8080   # matches redirect_uris in Step 5; add to your shell profile to persist
claude
```

That is the whole fix — `http://localhost:8080/callback` is registered, the port no longer rotates, and sign-in reaches the callback.

<details>
<summary>Registering a different / additional port after the fact</summary>

If you must use a port other than `8080`, register it. Okta app updates are a full-object `PUT`, so fetch the app, append your callback to `redirect_uris`, and put it back:

```bash
APP_INTERNAL_ID=$(curl -s "https://${OKTA_DOMAIN}/api/v1/apps?q=my-gateway-mcp-client&limit=1" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

CC_CALLBACK="http://localhost:8080/callback"   # the port you pinned above

curl -s "https://${OKTA_DOMAIN}/api/v1/apps/${APP_INTERNAL_ID}" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
| CC_CALLBACK="$CC_CALLBACK" python3 -c '
import sys, json, os
app = json.load(sys.stdin)
uris = app["settings"]["oauthClient"].setdefault("redirect_uris", [])
cb = os.environ["CC_CALLBACK"]
if cb not in uris:
    uris.append(cb)
json.dump(app, sys.stdout)
' \
| curl -s -X PUT "https://${OKTA_DOMAIN}/api/v1/apps/${APP_INTERNAL_ID}" \
    -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
    -H "Content-Type: application/json" -d @- \
| python3 -c 'import sys,json; print("redirect_uris:", json.load(sys.stdin)["settings"]["oauthClient"]["redirect_uris"])'
```

The final line echoes the app's redirect URIs so you can confirm your callback is listed.
</details>

### Assign users to the client app (required)

Okta issues **no token to a user who is not assigned to the client app** — an unassigned user gets `access_denied` / `User is not assigned to the client application` at `/authorize`, after the redirect URI is already correct. Assign yourself (or a group) to the app (*Applications → your app → Assignments → Assign*, or the API):

```bash
USER_ID=$(curl -s "https://${OKTA_DOMAIN}/api/v1/users?q=<your-okta-login-email>&limit=1" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')

curl -s -X POST "https://${OKTA_DOMAIN}/api/v1/apps/${APP_INTERNAL_ID}/users" \
  -H "Authorization: SSWS ${OKTA_TOKEN}" -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"${USER_ID}\"}" \
| python3 -c 'import sys,json; d=json.load(sys.stdin); print("scope:", d.get("scope"), "status:", d.get("status")) if "id" in d else print(json.dumps(d, indent=2))'
```

A successful assignment prints `scope: USER status: ACTIVE`. If your Step 3 rule scoped `clients.include` to a specific client rather than `ALL_CLIENTS`, add `$CLIENT_APP_ID` to that policy now too.

## Step 6: Connect your MCP client

Add the gateway as an MCP server in Claude Code, pinning the OAuth callback port to the one you registered (see [Redirect URIs for Claude Code](#redirect-uris-for-claude-code)):

```bash
export MCP_OAUTH_CALLBACK_PORT=8080
claude mcp add my-okta-gateway \
  "${GATEWAY_URL}/mcp" \
  --transport http \
  --client-id "$CLIENT_APP_ID"
```

Restart Claude Code (or run `/mcp`). Claude Code will:

1. Fetch the PRM at `/.well-known/oauth-protected-resource`.
2. Discover the Okta authorization server (`$ISSUER`).
3. Open a browser for sign-in (or use device code flow), redirecting to `http://localhost:8080/callback`.
4. Acquire a token with `scope=access_as_user offline_access` (and `aud=api://agentcore-gateway`).
5. Call the gateway with the token.

## Verification

### Check the PRM

Make sure `GATEWAY_URL` is without `/mcp`:

```bash
curl -s "${GATEWAY_URL}/.well-known/oauth-protected-resource" | python3 -m json.tool
```

Expected:

```json
{
    "authorization_servers": [
        "https://<OKTA_DOMAIN>/oauth2/<AS_ID>"
    ],
    "resource": "https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
    "scopes_supported": [
        "access_as_user"
    ]
}
```

Note `scopes_supported` is the short `access_as_user` — the same string the client requests and the same string that lands in the token's `scp` claim.

### Check the 401 WWW-Authenticate header

```bash
curl -s -D - -o /dev/null -X POST "${GATEWAY_URL}/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' \
  | grep -iE '^HTTP/|www-authenticate'
```

Expected:

```
HTTP/2 401
www-authenticate: Bearer error="invalid_token", scope="access_as_user", resource_metadata="https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/.well-known/oauth-protected-resource"
```

## Security Considerations

### 1. The client ID is not a secret

The client app (`my-gateway-mcp-client`) is a public client (`token_endpoint_auth_method: none`, PKCE). This is intentional: native/desktop MCP clients cannot securely store a secret.

> [!WARNING]
> Anyone who knows the client ID can start an OAuth flow against your org. They still need valid user credentials to finish sign-in, so this is not a token-theft vector — but do not treat the client ID as an access-control boundary. Control access with the authorization server's access policy, group assignments, and Okta [sign-on / network policies](https://developer.okta.com/docs/guides/configure-signon-policy/main/).

### 2. The access policy is your access-control boundary

Access is governed by the Custom Authorization Server's **access policy and rule** (Step 3) plus **app assignment** (Step 5) — those two together decide who can obtain gateway tokens.

> [!IMPORTANT]
> The tutorial's `ALL_CLIENTS` + `EVERYONE` rule lets any assigned user of any client obtain gateway tokens — appropriate for internal developer tooling. To restrict access, scope the rule's `conditions.clients.include` to specific client app IDs and `conditions.people.groups.include` to specific groups, and assign only those users to the client app.

### 3. Audience validation

The token `aud` is the authorization server's `audiences` value. Set `allowedAudience` to exactly that string.

> [!CAUTION]
> If `allowedAudience` does not match the authorization server's `audiences`, every token is rejected. If you point `discoveryUrl` at the **org** authorization server (missing `/oauth2/<AS_ID>/`) the issuer and audience model differ and validation against your custom scope fails. Always use the Custom Authorization Server's discovery URL and its audience.

### 4. Why there is no `advertisedScopeMapping`

The gateway validates and advertises the same short scope (`access_as_user`) because Okta issues that short form in `scp`. You only need `advertisedScopeMapping` if a downstream/broker authorization server puts a different string in the token than the one you want clients to request.

> [!CAUTION]
> If you ever add a mapping, keep every `allowedScopes` entry mapped. An unmapped scope is advertised in its raw form; a client that requests that raw form directly (bypassing the PRM) could authenticate with a scope you did not intend to expose. This fails closed for missing mappings but is worth auditing.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `The 'redirect_uri' parameter must be a Login redirect URI` at sign-in, repeatedly on different ports | Claude Code picks a new random callback port each attempt; registering one port doesn't help the next | Pin the port with `export MCP_OAUTH_CALLBACK_PORT=8080` and keep `http://localhost:8080/callback` in the app's `redirect_uris` (Step 5). |
| `access_denied` / `User is not assigned to the client application` (redirect URI already correct) | The signed-in Okta user is not assigned to the client app | Assign the user (or a group) to the app — see [Assign users to the client app](#assign-users-to-the-client-app-required). |
| `Policy evaluation failed for this request` at `/authorize` (user assigned, redirect URI correct) | The access-policy rule's `scopes.include` is missing a scope the client requests — most often `offline_access` | Add every requested scope (`access_as_user`, `openid`, `offline_access`) to the rule — see the Step 3 callout. |
| `access_denied` / policy error at `/authorize` even though scope and client exist | No matching access policy/rule on the Custom Authorization Server at all | Complete Step 3; if the rule is client-scoped, add the client to the policy. |
| `insufficient_scope` (HTTP 403) though token `scp` matches `allowedScopes` | `discoveryUrl` points at the **org** authorization server or the wrong `AS_ID`, so issuer/keys don't match | Use `https://<domain>/oauth2/<AS_ID>/.well-known/openid-configuration`. |
| Token `aud` not matching `allowedAudience` | `allowedAudience` is not the authorization server's `audiences` value (e.g. someone set it to the gateway URL) | Set `allowedAudience` to the AS `audiences` string (`api://agentcore-gateway`). |
| `scopes_supported` empty in PRM | `allowedScopes` not set on the gateway authorizer | Update the gateway with `allowedScopes`. |
| `POST /authorizationServers` returns an error | API Access Management add-on not enabled on the org | Enable API Access Management, or reuse the built-in `default` Custom Authorization Server (Step 1 tip). |
| PRM `resource` does not match the URL the client connected to | Gateway auto-generates `resource` as `<gw-url>/mcp`; a client connecting to a different path won't match | Connect to the gateway root `/mcp` path, or front with CloudFront and synthesize a corrected PRM. |

## Documentation

- [Build a Custom Authorization Server](https://developer.okta.com/docs/guides/customize-authz-server/main/)
- [Create scopes](https://developer.okta.com/docs/guides/customize-authz-server/main/#create-scopes) and [access token scopes / `scp` claim](https://developer.okta.com/docs/reference/api/oidc/#access-token-scopes-and-claims)
- [Create access policies and rules](https://developer.okta.com/docs/guides/configure-access-policy/main/)
- [Authorization Servers API](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/AuthorizationServer/) and [Applications API](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/Application/)
- [OpenID Connect & OAuth 2.0 API](https://developer.okta.com/docs/reference/api/oidc/)
- [Implement authorization code with PKCE](https://developer.okta.com/docs/guides/implement-grant-type/authcodepkce/main/)
- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)

---

> [!NOTE]
> This flow has been run end-to-end against a live Okta org and AgentCore Gateway with Claude Code as the MCP client. The described Okta behavior held: the token audience is fixed by the Custom Authorization Server, Okta ignores the RFC 8707 `resource` parameter Claude Code sends, scopes are short-form in `scp`, and **no `advertisedScopeMapping` was needed** — the gateway validated and advertised `access_as_user` unchanged. The gotchas that bit during that run are captured above: pin `MCP_OAUTH_CALLBACK_PORT`, assign the user to the client app, and include `offline_access` in the access-policy rule. Adjust `<REGION>`, `<ACCOUNT_ID>`, `<GATEWAY_SERVICE_ROLE>`, the callback port, and audience/scope names to your environment.
