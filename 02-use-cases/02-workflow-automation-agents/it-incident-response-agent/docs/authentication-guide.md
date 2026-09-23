# Authentication & Identity Guide

This project demonstrates three authentication patterns using AgentCore Identity.
Each pattern solves a different problem at a different boundary in the system.

## Overview: The 3 Auth Boundaries

```
┌─────────────────────┐       ┌─────────────────────┐       ┌─────────────────────┐
│  1. INBOUND AUTH    │       │  2. GATEWAY AUTH    │       │  3. JIRA AUTH       │
│                     │       │                     │       │                     │
│  Who can invoke     │──────▶│  How does the agent │──────▶│  How does the agent │
│  my agent?          │       │  call the Gateway?  │       │  act in Jira?       │
│                     │       │                     │       │                     │
│  Caller → Runtime   │       │  Runtime → Gateway  │       │  Runtime → Atlassian│
└─────────────────────┘       └─────────────────────┘       └─────────────────────┘
```

Each boundary is independent. You can mix and match:
- AWS_IAM inbound + AWS_IAM gateway + no Jira (simplest)
- AWS_IAM inbound + CUSTOM_JWT gateway + Jira 3LO (most complex)
- CUSTOM_JWT inbound + AWS_IAM gateway + no Jira (frontend calling agent)

---

## Boundary 1: Runtime Inbound Auth

**Question**: "Who is allowed to call my agent?"

### SigV4 (always available)

The caller signs HTTP requests using AWS credentials with service name
`bedrock-agentcore`. This is how all AWS-to-AWS communication works.

**Who uses this**:
- The trigger Lambda (SNS → Lambda → invoke Runtime)
- `agentcore invoke` CLI command
- `agentcore dev` Web UI (internally)
- Any backend service with IAM credentials

**Setup**: Nothing — it's always on. The Runtime only accepts signed requests.

**Code location**: No agent code needed. The Runtime framework validates SigV4
before your `@app.entrypoint` function is called.

### CUSTOM_JWT (opt-in addition)

The caller passes a JWT from an external Identity Provider (Auth0, Google, Okta,
Microsoft Entra). The Runtime validates the token against configured JWKS.

**Who uses this**:
- A React/Next.js frontend calling the agent directly
- A mobile app where you want user-scoped identity
- Any client that authenticates users via OIDC

**Setup**:
1. Configure the Gateway authorizer to accept JWTs from your IdP
2. Set `GATEWAY_AUTH_MODE=CUSTOM_JWT` in your environment
3. Store M2M credentials via `agentcore add credential`

**Code location**: `app/ITIncidentAgent/mcp_client/client.py` →
`_create_custom_jwt_client()`

**What happens at runtime**:
```python
@requires_access_token(
    provider_name="auth0-m2m",   # Credential stored in AgentCore Identity
    auth_flow="M2M",             # Client Credentials grant
    scopes=[],
)
def _build_client(*, access_token: str) -> MCPClient:
    # access_token is injected by the decorator
    # Agent code never sees the client_secret
    return MCPClient(
        lambda: streamablehttp_client(GATEWAY_URL, headers={"Authorization": f"Bearer {access_token}"})
    )
```

**Value demonstrated**: AgentCore Identity handles the entire OAuth flow —
secret storage, token exchange, caching, and refresh. The agent code just
decorates a function and receives the token.

### Key difference

| Aspect | SigV4 | CUSTOM_JWT |
|--------|-------|-----------|
| Identity source | AWS IAM (role/user) | External IdP (Auth0, Okta, etc.) |
| Token type | AWS Signature V4 headers | Bearer JWT |
| User identity | IAM principal ARN | JWT claims (sub, email, etc.) |
| Setup effort | Zero (IAM role exists) | Configure IdP + credentials |
| Best for | Backend/service-to-service | Frontend/user-facing apps |

---

## Boundary 2: Gateway Outbound Auth

**Question**: "How does my agent authenticate when calling the AgentCore Gateway?"

The Gateway is an MCP server that hosts your tools (Lambda functions). The agent
connects to it via streamable HTTP. But the Gateway needs to verify the agent is
authorized.

### AWS_IAM mode (default)

The agent signs every MCP request with SigV4 using its Runtime execution role.

```python
# mcp_client/client.py → _create_sigv4_auth()
class _SigV4Auth(httpx.Auth):
    def auth_flow(self, request):
        # Signs every request dynamically (not a one-shot pre-sign)
        credentials = botocore_session.get_credentials().get_frozen_credentials()
        SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(aws_request)
        yield request
```

**Setup**: Set `GATEWAY_AUTH_MODE=AWS_IAM` (or leave unset — it's the default).

**How it works**:
1. Agent runs in a container with an IAM execution role
2. Role has `bedrock-agentcore:InvokeGateway` permission
3. Every MCP request (tool list, tool call) is SigV4-signed
4. Gateway validates the signature → allows the call

### CUSTOM_JWT mode

The agent fetches an OAuth M2M token from AgentCore Identity, then passes it
as a Bearer token to the Gateway.

**Setup**:
1. Create an M2M application in your IdP (e.g., Auth0)
2. Store credentials: `agentcore add credential --name auth0-m2m --type oauth ...`
3. Set env vars:
   ```
   GATEWAY_AUTH_MODE=CUSTOM_JWT
   GATEWAY_OAUTH_PROVIDER_NAME=auth0-m2m
   GATEWAY_OAUTH_AUDIENCE=https://your-api-identifier
   ```

**How it works**:
1. Agent calls `@requires_access_token` → AgentCore Identity does client_credentials grant
2. Identity returns an access token (agent never sees the client_secret)
3. Agent passes `Authorization: Bearer <token>` to the Gateway
4. Gateway validates the JWT against the configured issuer/audience

### When to use which

| Scenario | Recommended mode |
|----------|-----------------|
| Everything stays within AWS | AWS_IAM |
| Gateway needs to trust tokens from Auth0/Okta/Entra | CUSTOM_JWT |
| You have a corporate policy requiring OIDC | CUSTOM_JWT |
| Quick POC / dev environment | AWS_IAM |
| Production with external identity provider | CUSTOM_JWT |

### The env var toggle

```bash
# In .env (for deploy) or agentcore/.env.local (for local dev)
GATEWAY_AUTH_MODE=AWS_IAM                      # Simple, zero external deps
GATEWAY_AUTH_MODE=CUSTOM_JWT                   # Enterprise IdP integration
GATEWAY_OAUTH_PROVIDER_NAME=auth0-m2m          # Which credential to use
GATEWAY_OAUTH_AUDIENCE=https://your-api-id     # API audience for token
```

The agent code in `client.py` reads this at startup and picks the right auth
strategy. No code changes needed — just a config flip.

---

## Boundary 3: Jira / Atlassian Outbound Auth (USER_FEDERATION)

**Question**: "How does my agent act as a human user in Jira?"

This is the most complex auth pattern. The agent needs to:
- Read Jira issues
- Add comments (attributed to a real person)
- Transition issue status

These actions need to happen *as a specific Jira user*, not as a generic
service account.

### OAuth 2.0 Authorization Code (3LO)

"3LO" = Three-Legged OAuth. The three legs are:
1. **User** (grants consent)
2. **Agent** (acts on user's behalf)
3. **Atlassian** (resource server)

**Setup**:
1. Create an OAuth 2.0 (3LO) app at developer.atlassian.com
2. Add scopes: `read:me`, `read:jira-user`, `read:jira-work`, `write:jira-work`, `offline_access`
3. Set env vars:
   ```
   JIRA_OAUTH_CLIENT_ID=your-client-id
   JIRA_OAUTH_CLIENT_SECRET=your-secret
   JIRA_SITE_URL=https://your-tenant.atlassian.net
   JIRA_PROJECT_KEY=INC
   ```
4. Deploy → CDK creates an AgentCore Identity OAuth2 provider
5. First invocation logs a consent URL → human opens it, approves
6. AgentCore caches the refresh token → all future calls are non-interactive

**Code location**: `app/ITIncidentAgent/mcp_client/jira.py`

**How it works at runtime**:
```
Agent → @requires_access_token(auth_flow="USER_FEDERATION")
     → AgentCore Identity checks for cached refresh token
     → If valid: returns access_token immediately
     → If expired: uses refresh_token to get a new one
     → If no token: logs consent URL (one-time human step)
     → Agent passes token to Atlassian MCP server
     → Atlassian sees actions as "User X did this"
```

### Why not just use a service account?

| Approach | Drawback |
|----------|----------|
| Service account API key | Actions attributed to a bot, not auditable per-user |
| Hardcoded OAuth token | Expires, no refresh, secret management nightmare |
| AgentCore USER_FEDERATION | Actions attributed to real user, auto-refresh, secret-free |

### Value demonstrated

AgentCore Identity manages the full lifecycle:
- **Secret storage**: client_secret never touches agent code or env vars
- **Token refresh**: `offline_access` scope + automatic refresh
- **Consent management**: One-time human approval, then fully autonomous
- **Audit trail**: Jira shows "John Smith commented" not "Bot API commented"

---

## How They Work Together

Here's what happens during a full Jira-mode invocation:

```
1. SNS delivers ticket event
2. Trigger Lambda INVOKES Runtime (SigV4 — Boundary 1)
3. Agent code starts...
4. Agent connects to AgentCore GATEWAY (AWS_IAM or CUSTOM_JWT — Boundary 2)
   → Discovers tools: lookup_user, get_process_info, query_kb, create_change_request
5. Agent connects to Atlassian MCP (USER_FEDERATION 3LO — Boundary 3)
   → Discovers tools: getIssue, addComment, transitionIssue
6. Agent runs with all tools from both servers
7. Agent calls lookup_user via Gateway (Boundary 2 auth on every call)
8. Agent calls getIssue via Jira MCP (Boundary 3 auth on every call)
9. Agent calls addComment via Jira MCP (as the human user)
10. Done
```

---

## Local Development: Auth Implications

### `agentcore dev` Web UI (port 8081)

The Web UI invokes the container via SigV4 internally but does **not** pass the
`X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header. This means:

- ✅ **AWS_IAM gateway mode works** (no user identity needed for SigV4)
- ❌ **CUSTOM_JWT gateway mode fails** (needs workload access token → needs user-id)

### `curl` to runtime container (port 8082)

You can manually pass the required header:

```bash
curl -N http://localhost:8082/invocations \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-User-Id: test-user" \
  -d '{"prompt": "What can you help me with?"}'
```

- ✅ **AWS_IAM works** (with or without the header)
- ✅ **CUSTOM_JWT works** (header provides the user-id for token exchange)

### Recommendation for local dev

Use `GATEWAY_AUTH_MODE=AWS_IAM` in `agentcore/.env.local` for day-to-day
development. Switch to `CUSTOM_JWT` only when specifically testing the OAuth
integration flow.

---

## Configuration Reference

### Files involved

| File | Auth-related content |
|------|---------------------|
| `.env` | `GATEWAY_AUTH_MODE`, `OAUTH_PROVIDER_NAME`, `GATEWAY_AUDIENCE`, `JIRA_*` vars |
| `agentcore/.env.local` | Same vars but for `agentcore dev` container runtime |
| `app/ITIncidentAgent/mcp_client/client.py` | Gateway auth (SigV4 vs CUSTOM_JWT) |
| `app/ITIncidentAgent/mcp_client/jira.py` | Jira auth (USER_FEDERATION 3LO) |
| `agentcore/cdk/lib/cdk-stack.ts` | Gateway authorizer config, Jira OAuth provider creation |

### Environment variables

| Variable | Boundary | Values |
|----------|----------|--------|
| `GATEWAY_AUTH_MODE` | 2 (Gateway) | `AWS_IAM` (default) or `CUSTOM_JWT` |
| `GATEWAY_OAUTH_PROVIDER_NAME` | 2 (Gateway) | AgentCore credential name (e.g., `auth0-m2m`) |
| `GATEWAY_OAUTH_AUDIENCE` | 2 (Gateway) | OAuth API audience identifier |
| `JIRA_OAUTH_CLIENT_ID` | 3 (Jira) | Atlassian OAuth app client ID |
| `JIRA_OAUTH_CLIENT_SECRET` | 3 (Jira) | Atlassian OAuth app client secret |
| `JIRA_SITE_URL` | 3 (Jira) | `https://your-tenant.atlassian.net` |
| `JIRA_PROJECT_KEY` | 3 (Jira) | Jira project key (e.g., `INC`) |
| `JIRA_MCP_URL` | 3 (Jira) | Atlassian MCP endpoint (hardcoded to `https://mcp.atlassian.com/v1/sse` by CDK when `JIRA_OAUTH_CLIENT_ID` is set) |
| `JIRA_OAUTH_PROVIDER_NAME` | 3 (Jira) | AgentCore credential for Jira 3LO |

---

## Troubleshooting Auth Issues

| Symptom | Boundary | Cause | Fix |
|---------|----------|-------|-----|
| "Workload access token has not been set" | 2 | CUSTOM_JWT mode without User-Id header | Switch to AWS_IAM or add header in curl |
| "AccessDeniedException: GetResourceOauth2Token" | 2 | Credential doesn't exist in AgentCore | Run `agentcore add credential --name <provider>` |
| 403 from Gateway | 2 | Runtime role lacks InvokeGateway permission | Check IAM role policy in CloudFormation |
| "invalid_grant" from Jira | 3 | Refresh token expired or revoked | Re-consent: check logs for consent URL |
| "Atlassian consent required" | 3 | First-time setup not completed | Open the logged URL, approve scopes |
| Gateway MCP timeout | 2 | SigV4 signature expired (clock skew) | Ensure container time is synchronized |

---

## Why This Project Includes All Three Patterns

This is a **reference sample** for Amazon Bedrock AgentCore. The auth patterns
represent progressive complexity levels:

1. **AWS_IAM** → "I just need this to work within AWS" (most start here)
2. **CUSTOM_JWT (M2M)** → "My org uses Auth0/Okta and I need to integrate" (enterprise)
3. **USER_FEDERATION (3LO)** → "My agent must act as a human in external SaaS" (advanced)

Each pattern maps to a real customer scenario. Including all three in one sample
means customers can:
- Start with the simplest path (AWS_IAM everything)
- Upgrade to CUSTOM_JWT when their security team requires it
- Add Jira (or any OAuth-protected API) when they need external integrations

The toggle is always a **config change**, never a code rewrite.
