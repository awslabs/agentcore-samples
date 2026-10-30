# AgentCore Gateway Inbound Auth with Microsoft Entra ID

This guide walks through setting up a new AgentCore Gateway with CUSTOM_JWT inbound authorization backed by Microsoft Entra ID. By the end, an MCP client (Claude Code, Codex, Kiro, etc.) can connect to your gateway, discover OAuth metadata via RFC 9728, acquire a token from Entra, and invoke MCP tools.

![claude](./claude-code.gif)

![kiro](./kiro.gif)

## Prerequisites

- Azure CLI (`az`) logged in with permissions to create/update App Registrations in your Entra tenant.
- AWS CLI (`aws`) with permissions to call `bedrock-agentcore-control` (CreateGateway, UpdateGateway).
- A target Entra tenant ID (run `az account show --query tenantId -o tsv` to confirm).

> [!TIP]
> This guide uses the Azure CLI and Microsoft Graph API. You can also perform all Entra steps through the [Entra admin center](https://entra.microsoft.com) (App registrations > your app > Expose an API / Authentication / API permissions).

## Overview

| Step | What happens |
|------|-------------|
| 1 | Create an Entra App Registration (the "resource app" representing your gateway API). |
| 2 | Expose an API scope on that app, set `requestedAccessTokenVersion: 2`. |
| 3 | Create the AgentCore Gateway with `CUSTOM_JWT`, `allowedAudience`, `allowedScopes`, and `advertisedScopeMapping`. |
| 4 | Add the gateway URL as an Application ID URI on the Entra app. |
| 5 | Register a client app that requests tokens for your gateway. |
| 6 | Connect your MCP client to the gateway. |

## Step 0: Recommend reading the Security Considerations first

Read more [here](#security-considerations).

## Step 1: Create the Entra App Registration (Resource App)

This app represents your gateway API. Tokens issued for this app are what the gateway validates.

```bash
APP_ID=$(az ad app create \
  --display-name "my-agentcore-gateway" \
  --sign-in-audience "AzureADMyOrg" \
  --query appId -o tsv)

echo "Application (client) ID: $APP_ID"
```

Create a service principal so the app is usable for token issuance:

```bash
az ad sp create --id "$APP_ID"
```

### Inspect the app

To see the full configuration of an existing app:

```bash
az ad app show --id "$APP_ID" --output json
```

Key fields to note:

| Field | Purpose |
|-------|---------|
| `appId` | The Application (client) ID. Used as `allowedAudience` on the gateway. |
| `identifierUris` | Application ID URIs. The gateway PRM `resource` value must be registered here. |
| `api.oauth2PermissionScopes` | Delegated permissions (scopes) you expose. |
| `api.requestedAccessTokenVersion` | Must be `2` to allow arbitrary identifier URIs (Step 4). |
| `signInAudience` | Who can sign in (`AzureADMyOrg` = single tenant). |

## Step 2: Expose an API Scope

Define a delegated permission scope that clients will request. This scope is what appears in the token's `scp` claim and what the gateway validates via `allowedScopes`.

This step also sets `requestedAccessTokenVersion: 2`, which is required in Step 4 to register the gateway URL as an identifier URI. Without it, Entra rejects `https://` URIs on domains you do not own.

> [!WARNING]
> If this is an existing app that already has clients in production, changing `requestedAccessTokenVersion` from null/1 to 2 is a **breaking change** for those clients. The `aud` claim format changes, which causes their token validation to fail. Only set this on new apps or after coordinating with all existing consumers.
> 
```bash
# Generate a UUID for the scope
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

# Get the Object ID (Graph API uses this, not the appId)
OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

# Set the Application ID URI, add the scope, and set token version to v2
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
```

> [!WARNING]
> The PATCH replaces the entire `oauth2PermissionScopes` list. If the app already has existing scopes, you must include them in the array. Omitting existing scopes removes them and breaks any clients that depend on them.

Verify the token version was set:

```bash
az ad app show --id "$APP_ID" --query 'api.requestedAccessTokenVersion' -o tsv
# Expected output: 2
```

The fully qualified scope string clients will request is:

```
api://<APP_ID>/access_as_user
```

### Multiple scopes

You can expose multiple scopes for fine-grained access control (e.g., `mcp.all`, `mcp.github`, `mcp.web_search`). Add each as a separate entry in `oauth2PermissionScopes` with a unique UUID. All scopes you want the gateway to validate must be listed in `allowedScopes` at gateway creation time.

---

## Step 3: Create the AgentCore Gateway

### 3a. Gather your Entra values

```bash
TENANT_ID=$(az account show --query tenantId -o tsv)
DISCOVERY_URL="https://login.microsoftonline.com/${TENANT_ID}/v2.0/.well-known/openid-configuration"

echo "Tenant:       $TENANT_ID"
echo "Discovery:    $DISCOVERY_URL"
echo "App ID:       $APP_ID"
```

> [!IMPORTANT]
> The discovery URL **must** include `/v2.0/` in the path. The v1.0 endpoint (`https://login.microsoftonline.com/{tenant}/.well-known/openid-configuration`) returns a different issuer format (`https://sts.windows.net/{tenant}/`) that does not match the `iss` claim in v2.0 tokens, causing scope validation to fail with `insufficient_scope`.

### 3b. Create the gateway

The gateway's auto-generated PRM at `/.well-known/oauth-protected-resource` includes a `scopes_supported` field. By default, it contains exactly the values from `allowedScopes`. With Entra, this creates a mismatch:

- Entra v2.0 tokens carry the **short form** of the scope in the `scp` claim (e.g., `access_as_user`).
- But MCP clients need to **request** the fully qualified URI scope from Entra (e.g., `api://<APP_ID>/access_as_user`).

If you only set `allowedScopes: ["access_as_user"]`, the PRM advertises `scopes_supported: ["access_as_user"]`. The client then requests `access_as_user` from Entra, which fails because Entra requires the fully qualified form on the `/authorize` request.

`advertisedScopeMapping` solves this by decoupling what the gateway **validates in the token** from what it **advertises to clients**:

- **Key** = the scope value the gateway checks in the token's `scp` claim (short form).
- **Value** = the scope the gateway advertises in PRM `scopes_supported` and `WWW-Authenticate` headers (fully qualified URI form that clients send to Entra).

```bash
aws bedrock-agentcore-control create-gateway \
  --region <REGION> \
  --name "my-gateway" \
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
      \"allowedAudience\": [\"$APP_ID\"],
      \"allowedScopes\": [\"access_as_user\"],
      \"advertisedScopeMapping\": {
        \"access_as_user\": \"api://$APP_ID/access_as_user\"
      }
    }
  }"
```

With this configuration:

- The gateway **validates** that the token contains scope `access_as_user` (the short form that Entra puts in the `scp` claim).
- The gateway **advertises** `api://<APP_ID>/access_as_user` in the PRM `scopes_supported` and in `WWW-Authenticate` headers.
- An MCP client reads the PRM, requests `api://<APP_ID>/access_as_user` from Entra, and Entra issues a token with `scp: access_as_user`. Both sides are satisfied.

Wait for the gateway to reach READY:

```bash
GATEWAY_ID=<gateway-id-from-output>
aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" --region <REGION> \
  --query '{status:status, url:gatewayUrl}' --output json
```

Save the gateway URL for subsequent steps:

```bash
GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier "$GATEWAY_ID" --region <REGION> \
  --query 'gatewayUrl' --output text)

echo "Gateway URL: $GATEWAY_URL"
```

### Understanding the authorizer fields

| Field | What it does |
|-------|-------------|
| `discoveryUrl` | Points at your Entra tenant's OIDC configuration. The gateway fetches signing keys and issuer info from here. |
| `allowedAudience` | Array of `aud` claim values the gateway accepts. Use your Entra app's Application (client) ID (the GUID). With v2.0 tokens, Entra sets `aud` to the GUID, not the full identifier URI. |
| `allowedScopes` | Array of scope values the gateway validates in the token's `scp` claim. At least one scope in the token must match. These values also appear as-is in the PRM `scopes_supported` (unless overridden by `advertisedScopeMapping`). |
| `advertisedScopeMapping` | A string-to-string map. Each key is a scope from `allowedScopes` (what the gateway validates). Each value is the corresponding scope advertised to clients in the PRM and `WWW-Authenticate` headers. Scopes without a mapping entry appear unchanged. |

### When to use `advertisedScopeMapping`

| Scenario | Use mapping? | Example |
|----------|-------------|---------|
| Entra token `scp` claim matches what the client requests (both use short form `access_as_user`) | No | `allowedScopes: ["access_as_user"]` |
| Client must request fully qualified URI scope from Entra, but token carries the short form | Yes | Key=`access_as_user`, Value=`api://<APP_ID>/access_as_user` |
| A broker AS requires a specific scope string different from what ends up in the token | Yes | Key=`<token-scope>`, Value=`<broker-required-scope>` |

---

## Step 4: Register the Gateway URL as an Application ID URI

The gateway's PRM at `/.well-known/oauth-protected-resource` contains a `resource` field:

```
https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp
```

MCP clients send this value as the RFC 8707 `resource` parameter on the `/authorize` request. Entra rejects the request with `AADSTS9010010` (resource/scope mismatch) unless this URL is registered as an Application ID URI on your resource app.

### Why this works

In Step 2 you set `requestedAccessTokenVersion: 2`. This relaxes Entra's identifier URI validation, allowing both `api://` and `https://` URIs with arbitrary domains (including `amazonaws.com`). Without v2, Entra requires a verified custom domain and rejects gateway URLs.

### Register the gateway URL

```bash
OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)

az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/applications/$OBJECT_ID" \
  --headers "Content-Type=application/json" \
  --body "{
    \"identifierUris\": [
      \"api://$APP_ID\",
      \"${GATEWAY_URL}/mcp\"
    ]
  }"
```

Verify:

```bash
az ad app show --id "$APP_ID" --query identifierUris -o json
```

Expected output:

```json
[
  "api://<APP_ID>",
  "https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp"
]
```

### What `requestedAccessTokenVersion: 2` changes

| Behavior | v1 (default/null) | v2 |
|----------|-------------------|----|
| Token format | v1.0 JWT | v2.0 JWT |
| `aud` claim in token | App ID URI (full string) | App ID (GUID) |
| Identifier URI validation | Strict (verified domain required) | Relaxed (arbitrary domains allowed) |

> [!CAUTION]
> With v2.0 tokens, the `aud` claim is the **GUID** (e.g., `9a74e830-...`), not the full identifier URI. If you set `allowedAudience` to the full URI (`https://...` or `api://...`), token validation will fail silently. Always use the GUID form in `allowedAudience`.

### Notes on identifier URI rules

- With `requestedAccessTokenVersion: 2`, both `api://` and `https://` URIs with any domain are accepted.
- Without it, only `api://<appId>`, `api://<tenantId>/<string>`, or URIs using a verified/initial domain work.
- The URI must be globally unique across all Entra tenants. If another app already has the same URI, the PATCH fails with `ObjectConflict`.
- Trailing slashes matter. `https://example.com/mcp` and `https://example.com/mcp/` are different identifiers.

## Step 5: Register a Client App

MCP clients need a `client_id` to authenticate. Create a dedicated public client app for your MCP clients (Claude Code, Codex, Kiro).

### Create the client app

```bash
CLIENT_APP_ID=$(az ad app create \
  --display-name "my-gateway-mcp-client" \
  --sign-in-audience "AzureADMyOrg" \
  --is-fallback-public-client true \
  --query appId -o tsv)

az ad sp create --id "$CLIENT_APP_ID"
echo "Client App ID: $CLIENT_APP_ID"
```

### Grant the client permission to request the gateway scope

```bash
# Get the scope ID from the resource app
SCOPE_ID=$(az ad app show --id "$APP_ID" \
  --query "api.oauth2PermissionScopes[?value=='access_as_user'].id" -o tsv)

# Add the permission to the client app
az ad app permission add \
  --id "$CLIENT_APP_ID" \
  --api "$APP_ID" \
  --api-permissions "${SCOPE_ID}=Scope"

# Grant admin consent (so users are not prompted)
az ad app permission admin-consent --id "$CLIENT_APP_ID"
```

The `admin-consent` command may print:

```
Invoking `az ad app permission grant --id <CLIENT_APP_ID> --api <APP_ID>` is needed to make the change effective
```

Run the grant explicitly (the `--scope` flag is required):

```bash
az ad app permission grant --id "$CLIENT_APP_ID" --api "$APP_ID" --scope access_as_user
```

> [!NOTE]
> Without this grant command, the consent is declared but not activated. Tokens will fail with a consent-required error even though admin-consent appears to have succeeded.

### Add redirect URIs for the client

MCP clients (Claude Code, Codex, Kiro) use a localhost callback with a **random port**. Per RFC 8252, Entra ignores the port when matching localhost redirect URIs on public clients (`isFallbackPublicClient: true`). Register the localhost callback without a port:

```bash
az ad app update --id "$CLIENT_APP_ID" \
  --public-client-redirect-uris "http://localhost/callback"
```

This single URI matches `http://localhost:58394/callback`, `http://localhost:12345/callback`, etc., because Entra treats localhost redirects as loopback (RFC 8252 Section 7.3) and the port is ignored for public clients.

## Step 6: Connect Your MCP Client

Add the gateway as an MCP server in Claude Code:

```bash
claude mcp add my-gateway \
  "${GATEWAY_URL}/mcp" \
  --transport http \
  --client-id "$CLIENT_APP_ID"
```

Restart Claude Code (or run `/mcp` in an active session). Claude Code will:

1. Fetch the PRM at `/.well-known/oauth-protected-resource`.
2. Discover the Entra authorization server.
3. Open a browser for sign-in (or use device code flow).
4. Acquire a token with `scope=api://<APP_ID>/access_as_user` and `resource=<gateway-url>/mcp`.
5. Call the gateway with the token.

## Verification

Before connecting the MCP client, verify the gateway is configured correctly:

### Check the PRM

Make sure the `GATEWAY_URL` is without `/mcp`,

```bash
curl -s "${GATEWAY_URL}/.well-known/oauth-protected-resource" | python3 -m json.tool
```

Expected:

```json
{
    "authorization_servers": [
        "https://login.microsoftonline.com/<TENANT_ID>/v2.0"
    ],
    "resource": "https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/mcp",
    "scopes_supported": [
        "api://<APP_ID>/access_as_user"
    ]
}
```

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
www-authenticate: Bearer error="invalid_token", scope="api://<APP_ID>/access_as_user", resource_metadata="https://<GATEWAY_ID>.gateway.bedrock-agentcore.<REGION>.amazonaws.com/.well-known/oauth-protected-resource"
```

## Security Considerations

### 1. Client ID is not a secret

The client app (`my-gateway-mcp-client`) is registered with `isFallbackPublicClient: true` and has no client secret. This is intentional: native/desktop MCP clients (Claude Code, Codex, Kiro) cannot securely store a secret.

> [!WARNING]
> Anyone who knows the client ID can initiate an OAuth flow against your tenant. They still need valid user credentials to complete sign-in, so this is not a token-theft vector. However, do not rely on the client ID alone as an access control boundary. Use `allowedScopes`, `customClaims`, and Entra Conditional Access policies to control who can access the gateway.

**Mitigations:**

- Restrict which users can consent to the client app (Entra > Enterprise applications > User consent settings).
- Use Conditional Access to block sign-ins from unexpected locations, devices, or risk levels.
- If you need machine-to-machine access (no interactive user), create a separate **confidential client** with a client secret or certificate, and use the `client_credentials` grant instead.

### 2. Admin consent is org-wide

Running `az ad app permission grant` with `consentType: AllPrincipals` means every user in your tenant can obtain tokens for this gateway without individual consent prompts.

> [!IMPORTANT]
> This is appropriate for internal developer tooling where all tenant users should have access. For gateways that should be restricted to specific users or groups, do NOT grant admin consent. Instead, set `appRoleAssignmentRequired: true` on the resource app's service principal and assign specific users/groups.

To restrict access after the fact:

```bash
# Require explicit app role assignment
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv)
az rest --method PATCH \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID" \
  --headers "Content-Type=application/json" \
  --body '{"appRoleAssignmentRequired": true}'
```

After this, only users/groups explicitly assigned to the app can obtain tokens.

### 3. `advertisedScopeMapping` decouples validation from advertisement

The gateway validates the **key** (`access_as_user`) but advertises the **value** (`api://<APP_ID>/access_as_user`) to clients. A misconfiguration can create a false sense of security:

> [!CAUTION]
> If you advertise a scope that maps to a key the gateway does NOT include in `allowedScopes`, clients will request that scope, obtain a token, but the gateway will reject it with `insufficient_scope`. This is a usability bug, not a security hole (fails closed).
>
> The dangerous misconfiguration is the reverse: if `allowedScopes` contains a value that has NO mapping entry, the gateway validates it but advertises the raw short form. A client that happens to request that short form directly (bypassing the PRM) could authenticate with a scope the gateway did not intend to expose publicly.

**Best practice:** every entry in `allowedScopes` should have a corresponding `advertisedScopeMapping` entry. Do not leave unmapped scopes unless you intentionally want them visible in their raw form.

### 4. `requestedAccessTokenVersion: 2` is safe but has implications

Setting token version to v2 is the standard for modern Entra apps and is required to register the gateway URL as an identifier URI. It does NOT weaken authentication or authorization.

> [!NOTE]
> The relaxed identifier URI validation only affects what **your own tenant admin** can register on **your own apps**. It does not allow other tenants to claim your URIs (global uniqueness is still enforced). It does not change who can obtain tokens or what permissions those tokens carry.

What it does change:

- **`aud` claim format:** v2.0 tokens use the GUID, not the full URI. If you have existing middleware that validates `aud` against the full identifier URI string, it will break after this change.
- **Token size:** v2.0 tokens may include different optional claims. Verify your downstream services can handle the token format.
- **Backward compatibility:** if other apps in your tenant already request tokens for this resource app using v1.0 endpoints, their tokens will still be issued according to the app's `requestedAccessTokenVersion` setting (v2.0). This may break those clients if they expect v1.0 token format.

> [!WARNING]
> If this is an existing app that already has clients in production, changing `requestedAccessTokenVersion` from null/1 to 2 is a **breaking change** for those clients. The `aud` claim format changes, which causes their token validation to fail. Only set this on new apps or after coordinating with all existing consumers.


## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `HostNameNotOnVerifiedDomain` when setting identifier URI | `requestedAccessTokenVersion` is not set to `2` on the resource app. | Run Step 2 again to set `requestedAccessTokenVersion: 2` via Graph PATCH. |
| `insufficient_scope` (HTTP 403) even though token `scp` matches `allowedScopes` | The gateway `discoveryUrl` uses the v1.0 endpoint (missing `/v2.0/` in the path). The v1.0 OIDC config returns a different issuer, causing validation to fail. | Change `discoveryUrl` to `https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration`. |
| `AADSTS9010010`: resource does not match requested scopes | The gateway URL is not registered as an identifier URI on the resource app. Entra sees the `resource` and `scope` as belonging to different apps. | Complete Step 4 to register the gateway URL. |
| `AADSTS50011`: redirect URI does not match | The client app does not have the correct redirect URI registered. | Run `az ad app update --id $CLIENT_APP_ID --public-client-redirect-uris "http://localhost/callback"`. Ensure `isFallbackPublicClient` is `true`. |
| `ObjectConflict`: another object with same identifierUris | Another app in the tenant already has this URI registered. | Remove the URI from the other app first, then retry. |
| `scopes_supported` is empty in PRM | `allowedScopes` not set on the gateway authorizer config. | Update the gateway with `allowedScopes`. |
| Token `aud` claim not matching `allowedAudience` | With v2.0 tokens, `aud` is the GUID, not the full URI. | Ensure `allowedAudience` contains the app's GUID (not the full identifier URI). |
| `advertisedScopeMapping` not taking effect | Gateway not updated after adding the mapping. | Run `update-gateway` with the new authorizer configuration; wait for READY. |
| Consent required error | The permission grant was not finalized. | Run `az ad app permission grant --id $CLIENT_APP_ID --api $APP_ID --scope access_as_user`. |
| PRM `resource` does not match the URL the client connected to | Gateway auto-generates `resource` as `<gw-url>/mcp`. If the client connects to a different path (e.g., a target sub-path like `/my-target/`), it won't match. | Front with CloudFront and synthesize a corrected PRM, or connect to the gateway's root `/mcp` path. |
