# Architecture — Real-world Use Case 1 (Okta)

A deeper look at the design choices and the request lifecycle. Read after you've run the happy path end-to-end.

## Components

### Frontend — FastAPI BFF

**Responsibilities:**
- Run the Okta authorization-code flow (authlib handles PKCE + secret exchange against the Web App).
- Maintain a server-side session tied to a signed cookie.
- Forward user prompts to the deployed agent with the user's access token as Bearer.

**Key design decision: BFF over SPA-with-token.**
- A SPA holding an access token in localStorage would expose the token to every third-party script on the page. BFF keeps the token server-side.
- The browser holds only a signed session cookie.
- The BFF can add logging, rate-limiting, and retry logic without leaking them to the client.

### Agent — Strands, on AgentCore Runtime

**Responsibilities:**
- Accept POST `/invocations` with a user JWT in `Authorization`.
- Runtime validates the JWT's signature, issuer, and audience against Okta's OIDC discovery document before the handler runs.
- Inside the handler, call the `get_my_profile` tool which does the OBO exchange (for a custom-scope token) and calls `/v1/userinfo` with the inbound user token for the profile payload.
- Let the LLM compose a natural-language response.

**Key design decisions:**
- **OBO happens in a tool, not in the main handler.** The LLM gets a clean tool interface (`get_my_profile`); it doesn't need to know about AgentCore Identity APIs or Okta-specific exchange parameters.
- **Downstream token never reaches the LLM.** The tool returns parsed profile JSON. If we returned the token, it would land in the LLM's context window — a leak risk.
- **User JWT passes through, not around.** It enters the handler via Runtime context, flows into the tool as module state (not a function arg, because Strands tool signatures are part of the prompt), gets used once for OBO, then falls out of scope.

### AgentCore Identity

**Responsibilities:**
- Store the Service App's client credentials.
- Execute the RFC 8693 token-exchange OBO with Okta when the agent asks for `ON_BEHALF_OF_TOKEN_EXCHANGE`.
- Return the downstream-scoped token.

**Key design decisions:**
- **`CustomOauth2` provider with `TOKEN_EXCHANGE` grant.** Okta does not have a built-in vendor in AgentCore (unlike `MicrosoftOauth2`), so we wire it explicitly via `onBehalfOfTokenExchangeConfig.grantType = TOKEN_EXCHANGE` and `actorTokenContent = NONE` (Okta doesn't require an actor token).
- **Only the Service App lives here.** The Web App talks to Okta directly via authlib — it's a human-facing client that doesn't need AgentCore as a broker.

### Okta — Two app registrations

- **Web App** — the thing the browser signs into. Has a client secret (used server-side by the BFF), a redirect URI, and permission under the upstream access policy to use the Authorization Code grant.
- **Service App** — the confidential client that does OBO. Has the **Token Exchange** grant enabled and **Proof of possession** disabled. Its credentials live on the AgentCore credential provider, not in the agent code.

This split is the production pattern: different apps for different trust surfaces. The `local/` example combined the frontend role with a Native App for the same auth server; the real-world variant uses a Web App instead, because the BFF is a confidential client running server-side.

## Request lifecycle — end to end

Here's what happens when the user types "What is my email?" and clicks "Ask agent":

### 1. Browser → FastAPI BFF
```
POST /ask
Cookie: session=<signed session cookie>
Content-Type: application/x-www-form-urlencoded

prompt=What+is+my+email%3F
```

The BFF validates the session, retrieves the `access_token` stored during sign-in (`aud = OKTA_AUDIENCE`, `cid = FRONTEND_CLIENT_ID`), and builds an outgoing request.

### 2. FastAPI BFF → AgentCore Runtime
```
POST https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{agent-runtime}/invocations
Authorization: Bearer <user-access-token>
Content-Type: application/json

{"prompt": "What is my email?"}
```

### 3. AgentCore Runtime inbound auth
The Runtime's `customJwtAuthorizer` validates the JWT:
- Signature against Okta's JWKS (from the OIDC discovery document).
- `iss` matches Okta's issuer URL for the configured auth server.
- `aud` matches `OKTA_AUDIENCE`.
- `exp` hasn't passed.

If validation fails: Runtime returns 401 before the handler runs. If it passes, the handler is invoked with the JWT available in the request context.

### 4. Inside the handler
```python
user_jwt = auth_header.split(" ", 1)[1]
_current_user_jwt["token"] = user_jwt
response = agent(prompt)
```

The LLM sees the prompt. It has one tool (`get_my_profile`). It decides to call that tool.

### 5. Inside `get_my_profile`
```python
workload_token = ac_identity.get_workload_access_token_for_jwt(
    workloadName="obo-usecase1-okta-realworld",
    userToken=user_jwt,
)["workloadAccessToken"]

downstream_token = ac_identity.get_resource_oauth2_token(
    workloadIdentityToken=workload_token,
    resourceCredentialProviderName="obo-uc1-okta-realworld-actor",
    scopes=["agent.downstream"],
    oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    customParameters={
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
    },
    audiences=["api://default"],
)["accessToken"]
```

AgentCore Identity internally POSTs to Okta's `/v1/token`:
```
POST https://{OKTA_DOMAIN}/oauth2/{OKTA_AUTH_SERVER_ID}/v1/token
Authorization: Basic base64(<Service App client_id>:<client_secret>)

grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token={user-jwt}
subject_token_type=urn:ietf:params:oauth:token-type:access_token
audience=api://default
scope=agent.downstream
```

Okta returns a new access token with `cid = AGENT_CLIENT_ID` (actor rotated), the same `sub` (user identity preserved), and `scp = ["agent.downstream"]`.

### 6. `get_my_profile` calls `/v1/userinfo` with the *inbound user token*
```python
requests.get(
    f"https://{OKTA_DOMAIN}/oauth2/{OKTA_AUTH_SERVER_ID}/v1/userinfo",
    headers={"Authorization": f"Bearer {user_jwt}"},  # not the OBO'd token!
)
```

The downstream token from step 5 has `scp=agent.downstream` — not `openid` — so it cannot be used against `/v1/userinfo` (which requires `openid`). Instead, the tool reuses the inbound user token, which already has `openid profile email` from sign-in.

In a production deployment with a real downstream resource server accepting `agent.downstream`, step 6 would be the downstream API call and the OBO'd token would be the right one to send.

### 7. LLM composes the response
The tool returns `{profile: <userinfo JSON>, obo_proof: {sub, cid, scp, aud}}` to the LLM. The LLM picks the relevant profile field: *"Your email is alice@example.com."* The system prompt tells it not to discuss the `obo_proof` field unless the user explicitly asks about OBO or tokens.

### 8. Response flows back
Handler → Runtime → BFF → browser. The BFF renders `result.html`.

## Security notes

- **No secrets in the browser.** Web App client secret, downstream token, session signing key — all server-side only.
- **LLM doesn't see either token.** It sees the tool's structured output (profile JSON plus a small `obo_proof` snapshot of claims), nothing else.
- **Per-user authorization at Okta.** Even if the agent tried to look up someone else's profile via `/v1/userinfo`, Okta would return the profile for the token-holder's `sub` only.
- **Audit trail.** The OBO'd token's `sub` and `cid` identify the user and the actor respectively. Okta's sign-in and system logs can correlate the request back to both.

## How this differs from `local/`

| Aspect | `local/` | `real-world/` |
|---|---|---|
| Agent runs... | Inline in the script | On AgentCore Runtime |
| User sign-in... | `USER_FEDERATION` against a credential provider | Real Okta auth code flow via browser redirect |
| Frontend... | Doesn't exist — the script simulates it | FastAPI BFF with HTML templates |
| Token storage... | In-memory, one session | HTTP session cookies |
| Where OBO happens | Top-level script | Inside the agent handler |
| Frontend OAuth client type | Native App | Web App |
| Downstream API | None — just claim validation | OBO mints a custom-scope token (demonstrates exchange); `/v1/userinfo` called with inbound user token for profile data |
| Downstream scope | `oboe2e.apiC.read` (custom) | `agent.downstream` (custom) — Okta refuses OIDC scopes on Token Exchange |

## How this differs from the Entra real-world example

- **Downstream API.** Entra calls Microsoft Graph `/me` with the OBO'd token carrying `User.Read`. Okta refuses OIDC scopes on Token Exchange, so the Okta example does two calls: OBO mints an `agent.downstream` token (the production-realistic pattern — for your own resource server), and `/v1/userinfo` is called with the inbound user token for profile data. Entra's example pattern (one OBO'd token hitting the IdP's identity endpoint) is not achievable on Okta.
- **OBO protocol.** Entra uses RFC 7523 JWT Bearer (`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`). Okta uses RFC 8693 Token Exchange (`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`). The shape of the AgentCore credential provider reflects this.
- **Exchange-time parameters.** Entra configures everything on the credential provider (`MicrosoftOauth2` auto-wires OBO). Okta requires `subject_token_type` + `audiences` on **every** `get_resource_oauth2_token` call — configurable in `CustomOauth2` is not enough.
- **DPoP.** Okta API Service apps default to DPoP-required on newer tenants; must be explicitly disabled. Entra has no equivalent requirement.

## Production considerations (not in this example)

- **Secrets management.** `.env` is fine for demos; use AWS Secrets Manager + IAM roles in production.
- **Session store.** Default FastAPI session is cookie-backed — fine for single-instance; swap to Redis/DynamoDB for multi-instance.
- **Authlib state + refresh.** authlib manages state/nonce automatically. For refresh tokens, stand up a persistent cache rather than re-authenticating every hour.
- **Access-policy tightening.** In production, narrow the upstream access policy to specific groups of users, and narrow the OBO policy to specific scopes the agent is authorized for — not "Any scopes".
- **Runtime scaling.** A single agent container is enough for a demo. Runtime's built-in autoscaling handles scale; nothing in the OBO flow changes.
- **Secret rotation.** Both the Web App and Service App secrets need rotation policies. Rotating the Service App secret requires re-running `deploy/01_create_providers.py` after updating `.env`.
