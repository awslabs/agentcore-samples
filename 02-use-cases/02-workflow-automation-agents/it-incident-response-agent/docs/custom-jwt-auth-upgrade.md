# Upgrading to CUSTOM_JWT Auth (Auth0 / Google / OIDC)

This sample defaults to **AWS_IAM** gateway auth for zero-dependency quick start.
For production deployments requiring external identity providers, follow this
guide to upgrade to **CUSTOM_JWT**.

> **When does CUSTOM_JWT inbound to the Runtime make sense?** In an automated,
> event-driven pipeline the Runtime is invoked by a *service* (the Trigger
> Lambda), so **IAM/SigV4 is the correct inbound auth** — there's no human token
> at invoke time. CUSTOM_JWT inbound only fits when a logged-in human invokes the
> Runtime *directly* (e.g. an interactive "ask the agent" UI). User OAuth for
> ticket *submission* belongs at the ticket-ingress API, not the Runtime. See
> [ARCHITECTURE.md → "Choosing the inbound auth mode"](./ARCHITECTURE.md) for the
> full reasoning. This guide focuses on **CUSTOM_JWT for the Gateway hop**
> (agent → tools), which applies regardless of how the Runtime is invoked.

---

## Prerequisites

Before starting, you need:

1. **An OIDC-compatible Identity Provider** with a Machine-to-Machine (M2M) application:
   - **Auth0** (recommended for testing — free tier supports M2M)
   - **Google** (service account with OAuth2 client credentials)
   - **Okta**, **Microsoft Entra ID**, or any OIDC provider

2. **From your IdP, collect:**
   - Client ID
   - Client Secret
   - Discovery URL (the `.well-known/openid-configuration` endpoint)
   - API Audience / Identifier (what the token is scoped to)

3. **AgentCore CLI installed** (`npm install -g @aws/agentcore@0.30.0`)
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }

4. **AWS credentials** for your deployment account

---

## Quick Setup (one command)

```bash
./scripts/enable-custom-jwt.sh
```

This prompts for your IdP credentials, stores them securely in AgentCore
Identity, updates the gateway config, and deploys — all in one script.

---

## Manual Setup (step-by-step)

### Step 1: Create Your IdP Application

Choose your provider and create an M2M application:

**Auth0 (recommended for quick testing):**
1. Sign up at https://auth0.com (free tier works)
2. Go to **Applications → APIs → Create API**
   - Name: `IT Incident Response`
   - Identifier: `https://it-incident-response/api` (this becomes your `GATEWAY_OAUTH_AUDIENCE`)
3. Go to **Applications → Create Application → Machine to Machine**
   - Authorize it for the API you just created
4. Copy the **Client ID** and **Client Secret** from the application's Settings tab
5. Your discovery URL is: `https://YOUR_TENANT.us.auth0.com/.well-known/openid-configuration`

**Google:**
1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** (type: Web application)
3. Copy the Client ID and Client Secret
4. Discovery URL: `https://accounts.google.com/.well-known/openid-configuration`
5. Your `GATEWAY_OAUTH_AUDIENCE` is the Client ID itself

**Okta:**
1. In the Okta admin console, go to **Applications → Create App Integration**
2. Select **API Services** (machine-to-machine)
3. Copy Client ID and Client Secret
4. Discovery URL: `https://YOUR_DOMAIN.okta.com/.well-known/openid-configuration`

### Step 2: Store the Credential in AgentCore Identity

AgentCore Identity securely stores and manages your OAuth credentials. The agent
code **never sees the client_secret** — AgentCore performs the token exchange internally.

```bash
# Set AWS credentials first
# Ensure AWS credentials are configured (aws configure, SSO, or environment variables)

# Auth0 example:
agentcore add credential \
  --name auth0-m2m \
  --type oauth \
  --client-id YOUR_M2M_CLIENT_ID \
  --client-secret YOUR_M2M_CLIENT_SECRET \
  --discovery-url https://YOUR_TENANT.us.auth0.com/.well-known/openid-configuration

# Google example:
agentcore add credential \
  --name google-oauth \
  --type oauth \
  --client-id YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com \
  --client-secret GOCSPX-xxxxxxxxxxxxxxxxxxxxx \
  --discovery-url https://accounts.google.com/.well-known/openid-configuration

# Any OIDC provider:
agentcore add credential \
  --name my-idp \
  --type oauth \
  --client-id YOUR_CLIENT_ID \
  --client-secret YOUR_CLIENT_SECRET \
  --discovery-url https://YOUR_IDP/.well-known/openid-configuration
```

> **Important**: The `--name` value (e.g., `auth0-m2m`) must match the
> `GATEWAY_OAUTH_PROVIDER_NAME` environment variable exactly.

### Step 3: Set Environment Variables

In your `.env` file:

```bash
GATEWAY_AUTH_MODE=CUSTOM_JWT
GATEWAY_OAUTH_PROVIDER_NAME=auth0-m2m                       # must match credential name from Step 2
GATEWAY_OAUTH_AUDIENCE=https://it-incident-response/api     # your API identifier from Step 1
```

> **Note**: `CLIENT_SECRET` should NOT be in `.env` — it's stored securely in
> AgentCore Identity (Step 2). Remove it from `.env` if present.

### Step 4: Deploy

```bash
# Ensure AWS credentials are configured (aws configure, SSO, or environment variables)
agentcore deploy -y --target dev
```

The deploy will:
- Update the Runtime env vars (`GATEWAY_AUTH_MODE`, `GATEWAY_OAUTH_PROVIDER_NAME`, `GATEWAY_OAUTH_AUDIENCE`)
- The Gateway's CUSTOM_JWT authorizer validates incoming Bearer tokens via OIDC discovery
- Rebuild the container (if source code changed)

---

## Testing the OAuth Flow

### Test 1: Verify Code Path (no IdP needed)

Check that the dual-mode routing works by looking at logs during local dev:

```bash
# Export the auth vars
export GATEWAY_AUTH_MODE=CUSTOM_JWT
export GATEWAY_OAUTH_PROVIDER_NAME=auth0-m2m
export GATEWAY_OAUTH_AUDIENCE=https://it-incident-response/api

# Start local dev server
agentcore dev
```

Expected log output:
```
INFO  Using CUSTOM_JWT auth (provider: auth0-m2m)
ERROR Failed to fetch token — credential 'auth0-m2m' not available locally
```

This confirms the code correctly routes to the CUSTOM_JWT path. The token
fetch fails because AgentCore Identity is a cloud service (not available locally).

### Test 2: End-to-End with Deployed Runtime

After completing Steps 1–4 above:

```bash
# 1. Publish a test ticket
./scripts/publish_ticket.sh

# 2. Watch the agent process it
agentcore logs --since 5m
```

**Success indicators in the logs:**
```
INFO  Using CUSTOM_JWT auth (provider: auth0-m2m)
INFO  Connected to Gateway via MCP (StreamableHTTP)
INFO  Calling tool: lookup_user
INFO  Calling tool: get_process_info
INFO  Ticket INC-20260604-001 resolved successfully
```

**Failure indicators:**
```
ERROR CUSTOM_JWT mode requires OAUTH_PROVIDER_NAME env var    → OAUTH_PROVIDER_NAME not set
ERROR 401 Unauthorized                                        → Token invalid or expired
ERROR credential 'auth0-m2m' not found                        → Step 2 not completed
ERROR token exchange failed: invalid_client                   → Wrong client_id/secret
```

### Test 3: Verify Token Flow (manual inspection)

To verify the full token exchange is working:

```bash
# Check the credential exists in AgentCore
agentcore status

# Look for the credential in the output:
# Credentials:
#   auth0-m2m (oauth) ✓
```

### Test 4: Revert to AWS_IAM and Confirm Both Modes Work

```bash
# Switch back
sed -i '' 's/GATEWAY_AUTH_MODE=CUSTOM_JWT/GATEWAY_AUTH_MODE=AWS_IAM/' .env

# Redeploy
# Ensure AWS credentials are configured (aws configure, SSO, or environment variables)
agentcore deploy -y --target dev

# Test — should work with SigV4 auth now
./scripts/publish_ticket.sh
agentcore logs --since 5m
# Expected: "Using AWS_IAM auth (automatic SigV4)"
```

---

## Restricting Access by Claim (Audience / Clients / Scopes)

The Gateway's `customJwtAuthorizer` block does more than validate the token
signature — it **restricts which tokens are accepted** based on their claims.
This is how you limit who can invoke the agent's tools, even among callers
that hold a valid token from your IdP.

### The three claim filters

In `agentcore/agentcore.json`, the gateway's authorizer accepts three optional
allow-lists. A token must satisfy **all** configured filters to be accepted:

```json
{
  "name": "ITIncidentGateway",
  "authorizerType": "CUSTOM_JWT",
  "customJwtAuthorizer": {
    "discoveryUrl": "https://YOUR_TENANT.us.auth0.com/.well-known/openid-configuration",
    "allowedAudience": ["https://it-incident-response/api"],
    "allowedClients": ["abc123M2MClientId", "def456FrontendClientId"],
    "allowedScopes": ["resolve:tickets", "read:incidents"]
  }
}
```

| Field | Token claim checked | Rule | Use it to… |
|-------|--------------------|------|------------|
| `allowedAudience` | `aud` | Token's audience must match **one** of the listed values | Ensure the token was minted *for this API* and not some other service in your IdP |
| `allowedClients` | `client_id` | Token's issuing client must be **one** of the listed IDs | Restrict to specific applications (e.g. only your M2M client and your frontend SPA) |
| `allowedScopes` | `scope` | Token must contain **at least one** of the listed scopes | Enforce least-privilege — only tokens granted `resolve:tickets` may invoke |

> **Evaluation order**: signature → expiry → `allowedAudience` → `allowedClients`
> → `allowedScopes`. The first failed check rejects the request with `403`
> before any tool runs.

### Example: lock the agent to one client and one scope

Suppose only your backend M2M application (client `abc123`) should be able to
trigger ticket resolution, and only if its token carries the `resolve:tickets`
scope:

```json
"customJwtAuthorizer": {
  "discoveryUrl": "https://YOUR_TENANT.us.auth0.com/.well-known/openid-configuration",
  "allowedAudience": ["https://it-incident-response/api"],
  "allowedClients": ["abc123"],
  "allowedScopes": ["resolve:tickets"]
}
```

A token from a different client, or one missing the `resolve:tickets` scope,
is rejected at the Gateway — the agent's tools never execute.

### Configuring scopes in Auth0

1. In the Auth0 dashboard, open **Applications → APIs → your API → Permissions**.
2. Add a permission (scope), e.g. `resolve:tickets`.
3. Open **Applications → APIs → Machine to Machine Applications**, expand your
   M2M app, and grant it the `resolve:tickets` permission.
4. Auth0 will now include `"scope": "resolve:tickets"` in tokens issued to that
   M2M app via the `client_credentials` grant.

### Setting the filters

The `enable-custom-jwt.sh` script prompts for audience, client ID, and
(optionally) a space-separated list of scopes, and writes all three into the
`customJwtAuthorizer` block. To set scopes manually, edit `agentcore.json` as
shown above and run `agentcore deploy -y --target dev`.

> **Tip**: Omit a field entirely to skip that check. For example, leaving out
> `allowedScopes` accepts any scope; leaving out `allowedClients` accepts tokens
> from any client in your IdP (still constrained by `allowedAudience`).

---

## How It Works

```
Agent Runtime
    │
    ├─ Reads GATEWAY_AUTH_MODE env var
    │
    ├─ If CUSTOM_JWT:
    │     │
    │     ├─ mcp_client/client.py calls _create_custom_jwt_client()
    │     │     │
    │     │     └─ @requires_access_token(provider_name="auth0-m2m", auth_flow="M2M")
    │     │           │
    │     │           └─ AgentCore Identity performs client_credentials grant
    │     │              against the IdP's token endpoint
    │     │              (agent never sees client_secret)
    │     │           │
    │     │           └─ Returns JWT access token
    │     │
    │     └─ Connects to Gateway with: Authorization: Bearer <token>
    │           │
    │           └─ Gateway CUSTOM_JWT authorizer validates token via OIDC:
    │                1. Signature (against discoveryUrl JWKS)
    │                2. Expiry (exp claim)
    │                3. allowedAudience  → token 'aud' must match
    │                4. allowedClients   → token 'client_id' must match
    │                5. allowedScopes    → token 'scope' must contain one
    │              (any failed check → 403, tools never run)
    │
    ├─ If AWS_IAM (default):
    │     │
    │     └─ Connects to Gateway with automatic SigV4 signing
    │        (Runtime's IAM role handles this transparently)
    │
    └─ Tools execute normally via MCP protocol
```

---

## Supported Providers

| Provider | Discovery URL | Notes |
|----------|---------------|-------|
| **Auth0** | `https://TENANT.auth0.com/.well-known/openid-configuration` | Best for testing. Free tier supports M2M `client_credentials` grant natively. |
| **Google** | `https://accounts.google.com/.well-known/openid-configuration` | Use OAuth2 client credentials. `GATEWAY_AUDIENCE` = your client ID. |
| **Okta** | `https://DOMAIN.okta.com/.well-known/openid-configuration` | Create an API Services app for M2M. |
| **Microsoft Entra** | `https://login.microsoftonline.com/TENANT/v2.0/.well-known/openid-configuration` | Use app registration with client credentials. |
| **Any OIDC** | Your provider's discovery endpoint | Must support `client_credentials` grant type. |

> **Provider compatibility note**: The M2M flow uses the standard OAuth2
> `client_credentials` grant. Your IdP must support this grant type at its
> token endpoint. Auth0 supports this natively. Google requires using an
> OAuth2 client (not a service account) — ensure your Google client is
> configured for the `client_credentials` flow.

---

## Security Benefits

- Agent code **never sees** the client_secret (stored in AgentCore Identity)
- Token is short-lived (IdP-controlled expiry, typically 24h for M2M)
- Gateway validates every request (signature, expiry, audience claim)
- Credential rotation happens in AgentCore Identity, not in code
- Audit trail: AgentCore logs token issuance events
- No secrets in `.env` or environment variables

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| `credential 'X' not found` | Step 2 not completed or name mismatch | Run `agentcore add credential --name X ...` — name must match `GATEWAY_OAUTH_PROVIDER_NAME` exactly |
| `401 Unauthorized` from Gateway | Token invalid, expired, or wrong audience | Check that `GATEWAY_OAUTH_AUDIENCE` matches what's configured in your IdP |
| `403 Forbidden` from Gateway (token is valid) | Token failed a claim filter (`allowedAudience`, `allowedClients`, or `allowedScopes`) | Decode the token (jwt.io) and confirm its `aud`, `client_id`, and `scope` claims match the `customJwtAuthorizer` allow-lists in `agentcore.json` |
| `invalid_client` during token exchange | Wrong client_id or client_secret | Re-run `agentcore add credential` with correct values from your IdP |
| `CUSTOM_JWT mode requires GATEWAY_OAUTH_PROVIDER_NAME` | Env var not set | Add `GATEWAY_OAUTH_PROVIDER_NAME=<name>` to `.env` and redeploy |
| Token works but Gateway rejects it | Gateway authorizer not configured for CUSTOM_JWT | Ensure the deploy completed. Check `agentcore status` for gateway auth type. |
| Works deployed but fails in `agentcore dev` | AgentCore Identity is a cloud service | Token fetch only works in a deployed Runtime. For local dev, use `GATEWAY_AUTH_MODE=AWS_IAM`. |

---

## Reverting to AWS_IAM

```bash
# In .env:
GATEWAY_AUTH_MODE=AWS_IAM

# Deploy:
# Ensure AWS credentials are configured (aws configure, SSO, or environment variables)
agentcore deploy -y --target dev

# Optionally remove the credential:
agentcore remove credential --name auth0-m2m -y
```

No code changes needed — `mcp_client/client.py` automatically switches
based on the `GATEWAY_AUTH_MODE` environment variable.
