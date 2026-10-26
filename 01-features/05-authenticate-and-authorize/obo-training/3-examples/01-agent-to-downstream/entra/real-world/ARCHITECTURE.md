# Architecture — Real-world Use Case 1 (Entra)

A deeper look at the design choices and the request lifecycle. Read after you've run the happy path end-to-end.

## Components

### Frontend — FastAPI BFF

**Responsibilities:**
- Run the Entra auth code flow (MSAL handles the PKCE + secret exchange).
- Maintain a server-side session tied to a signed cookie.
- Forward user prompts to the deployed agent with the user's access token as Bearer.

**Key design decision: BFF over SPA-with-token.**
- A SPA holding an access token in localStorage would need CORS setup on the Runtime endpoint and would expose the token to every third-party script on the page. BFF keeps the token server-side.
- The browser only holds a `session` cookie (signed with `FRONTEND_SESSION_SECRET`).
- The BFF can add logging, rate-limiting, and retry logic without exposing them to the client.

### Agent — Strands, on AgentCore Runtime

**Responsibilities:**
- Accept POST `/invocations` with a user JWT in `Authorization`.
- Runtime validates the JWT's signature, issuer, and audience against the Entra OIDC discovery document before the handler runs.
- Inside the handler, call the `get_my_profile` tool which does the OBO + Graph call.
- Let the LLM compose a natural-language response.

**Key design decisions:**
- **OBO happens in a tool, not in the main handler.** The LLM gets a clean tool interface (`get_my_profile`); it doesn't need to know about AgentCore Identity APIs.
- **Graph token never reaches the LLM.** The tool returns already-parsed profile JSON. If we returned the token, it'd land in the LLM's context window — a leak risk.
- **User JWT passes through, not around.** It enters the handler via Runtime context, flows into the tool as a function argument, gets used once for OBO, then falls out of scope.

### AgentCore Identity

**Responsibilities:**
- Store the Entra agent app's client credentials.
- Execute the RFC 7523 JWT-bearer OBO exchange with Entra when the agent asks for `ON_BEHALF_OF_TOKEN_EXCHANGE`.
- Return the downstream-audienced Graph token.

**Key design decisions:**
- **Built-in `MicrosoftOauth2` provider.** Auto-configures the OBO grant type, so we don't need `CustomOauth2` + `onBehalfOfTokenExchangeConfig`. This is the simplest path for Entra.
- **Only the agent app is stored here.** The frontend app talks to Entra directly via MSAL — it's a human-facing client that doesn't need AgentCore as a broker.

### Entra ID — Two app registrations

- **Frontend app** — the thing the browser signs into. Has a client secret (used server-side by the BFF), a redirect URI, and permission to call the agent app's `access_as_user` scope.
- **Agent app** — the resource. Exposes `access_as_user`. Has delegated permission for Microsoft Graph `User.Read`. Its client secret lives in AgentCore Identity. Lists the frontend app as a knownClientApplication so consent is combined.

This split is the production pattern: different apps for different trust surfaces. The local example combined them because it's a single-process demo; the real-world example separates them.

## Request lifecycle — end to end

Here's what happens when the user types "What is my email?" and clicks "Ask agent":

### 1. Browser → FastAPI BFF
```
POST /ask
Cookie: session=<signed session cookie>
Content-Type: application/x-www-form-urlencoded

prompt=What+is+my+email%3F
```

The BFF validates the session, retrieves the `access_token` stored during sign-in (aud = agent app client ID), and builds an outgoing request.

### 2. FastAPI BFF → AgentCore Runtime
```
POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{agent-runtime}/invocations
Authorization: Bearer <user-access-token>
Content-Type: application/json

{"prompt": "What is my email?"}
```

### 3. AgentCore Runtime inbound auth
The Runtime's `customJWTAuthorizer` validates the JWT:
- Signature against Entra's JWKS (from the OIDC discovery document).
- `iss` matches Entra's issuer URL for the configured tenant.
- `aud` matches the agent app's client ID.
- `exp` hasn't passed.

If validation fails: Runtime returns 401 before the handler runs. If it passes, the handler is invoked with the JWT available in the request context.

### 4. Inside the handler
```python
user_jwt = context.identity_token        # the validated user JWT
response = agent(f"{prompt}\n\nUser JWT: {user_jwt}")
```
The agent (an LLM) sees the prompt + JWT. It has one tool (`get_my_profile`). It decides to call that tool with the JWT.

### 5. Inside `get_my_profile`
```python
workload_token = ac_identity.get_workload_access_token_for_jwt(
    workloadName="obo-usecase1-entra-realworld",
    userToken=user_jwt,
)["workloadAccessToken"]

graph_token = ac_identity.get_resource_oauth2_token(
    workloadIdentityToken=workload_token,
    resourceCredentialProviderName="obo-uc1-entra-realworld-actor",
    scopes=["https://graph.microsoft.com/User.Read"],
    oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
)["accessToken"]
```

AgentCore Identity internally POSTs to Entra's token endpoint:
```
POST https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
client_id={agent_client_id}
client_secret={stored in credential provider}
assertion={user-jwt}
scope=https://graph.microsoft.com/User.Read
requested_token_use=on_behalf_of
```

Entra returns a new access token with `aud = https://graph.microsoft.com`.

### 6. `get_my_profile` calls Graph
```python
requests.get(
    "https://graph.microsoft.com/v1.0/me",
    headers={"Authorization": f"Bearer {graph_token}"},
)
```
Graph validates the token, runs `/me` as the user whose `oid` is in the token, and returns the profile JSON.

### 7. LLM composes the response
The tool returns the profile to the LLM. The LLM picks out the relevant field and responds: *"Your email is alice@example.com."*

### 8. Response flows back
Handler → Runtime → BFF → browser. The BFF renders `result.html` with the agent's answer.

## Security notes

- **No secrets in the browser.** Client secret, Graph token, session signing key — all server-side only.
- **LLM doesn't see Graph token.** It sees the tool's structured output (profile JSON), nothing else.
- **Per-user authorization at Graph.** Even if the agent had been asked to look up someone else's profile, Graph would enforce that `/me` only returns the token-holder's record.
- **Audit trail.** The OBO'd token's `oid` and `appid` identify the user and the actor respectively. Graph's logs can correlate the request back to both.

## Production considerations (not in this example)

- **Secrets management.** `.env` is fine for demos; use AWS Secrets Manager + IAM roles in production.
- **Session store.** Default FastAPI session is cookie-backed — fine for single-instance; swap to Redis/DynamoDB for multi-instance.
- **MSAL token cache.** For refresh tokens, stand up a confidential MSAL app with a persistent cache (file, Redis) rather than re-authenticating every hour.
- **Conditional Access.** If the downstream target has CA policies (MFA step-up, device compliance), the OBO exchange can return `interaction_required`. Surface that back to the browser via a 401 + claims challenge.
- **Runtime scaling.** A single agent container is enough for a demo. For scale, use Runtime's built-in autoscaling; nothing in the OBO flow changes.
- **Secret rotation.** Both the Entra client secret and the session signing key need rotation policies. Rotating the Entra secret requires re-running `deploy/01_create_providers.py` (or updating the provider via `update-oauth2-credential-provider`).
