# Architecture — Real-world Use Case 2 (Okta)

A deeper look at the design choices, the three Okta app registrations, the two OBO exchanges, and the request lifecycle. Read after you've run the happy path end-to-end.

## What changes vs Use Case 1

UC1 (Okta) puts a single OBO hop **in the agent's tool code** — the agent uses AgentCore Identity to swap the user token for a downstream-scoped one and calls the downstream API directly. UC2 adds a second OBO hop **inside the Gateway**, and removes downstream access from the agent code path entirely.

| | Use Case 1 (Okta) | Use Case 2 (Okta) |
|---|---|---|
| Number of OBO exchanges | 1 (in agent) | 2 (in agent + in Gateway) |
| Agent calls the downstream API directly? | Yes — `requests.get(...)` in a `@tool` function | No — agent calls Gateway over MCP; Gateway calls downstream |
| Agent IAM permissions for OBO | `GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token` + `secretsmanager:GetSecretValue` | Same set (still does OBO #1) |
| Gateway needs IAM permissions? | n/a (no Gateway) | Yes — same set, on the Gateway service role |
| Okta app registrations | 2 (Web App + API Services) | 3 (Web App + 2× API Services) |
| Combined consent chain | Access policy on FrontendApp + access policy on AgentApp | Same, plus a third policy on GatewayApp |
| Downstream permission lives on... | Agent's Service App via `agent.downstream` scope policy | Gateway's Service App via `downstream.access` scope policy |
| What the LLM "sees" of tokens | Nothing — tool returns parsed JSON | Nothing — MCP tool returns parsed JSON |

The headline takeaway: **the same OBO primitive composes across layers**. Once you've built one OBO hop in code, you can put another in infrastructure, and the chain stays cryptographically traceable via the `sub` claim.

## Components

### Frontend — FastAPI BFF (authlib)

Runs the Okta auth code flow with PKCE. Holds the user's access token server-side in a signed session cookie. Forwards prompts to the agent with that token as Bearer.

**Token it holds:** `T_user`. `aud = OKTA_AUDIENCE`, `cid = FRONTEND_CLIENT_ID`, `sub = <user login>`, `scp` includes `agent.access` plus the OIDC scopes for sign-in.

### Agent — Strands, on AgentCore Runtime

**Inbound:** Runtime's `customJWTAuthorizer` validates `T_user` against Okta OIDC (signature, issuer, audience = OKTA_AUDIENCE).

**Inside the handler:**
1. Read `T_user` from `context.request_headers["Authorization"]`.
2. Perform **OBO #1** via AgentCore Identity:
   - `GetWorkloadAccessTokenForJWT(workloadName=agent-workload, userToken=T_user)`
   - `GetResourceOauth2Token(provider=agent-obo-provider, oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE, scopes=[gateway.access], customParameters={subject_token_type: ...}, audiences=[OKTA_AUDIENCE])`
   - Returns `T_gateway`. `aud = OKTA_AUDIENCE` (unchanged), `cid = AGENT_CLIENT_ID` (rotated), `sub = <same user>` (constant), `scp = [gateway.access]`.
3. Open an MCP client connection to the Gateway, presenting `T_gateway` as Bearer.
4. List + invoke tools. The LLM picks the `callDownstreamApi` tool exposed by the Gateway.
5. Return the tool result to the LLM. LLM composes the answer.

**What the agent code does NOT do:** call the downstream API directly. There's no `requests.get(...)` in the agent codebase.

### AgentCore Gateway — OpenAPI target → mock downstream

**Inbound auth:** `customJWTAuthorizer` configured with Okta's OIDC discovery and `allowedAudience = [OKTA_AUDIENCE]`. Validates `T_gateway` on every MCP call.

Note that both the Runtime AND the Gateway configure the **same** `allowedAudience` — Okta's default authorization server mints every token with the same `aud` regardless of which client requested it. The two hops are differentiated by SCOPE (`agent.access` vs `gateway.access`), not by audience. This is fundamentally different from Entra, where each app has its own audience.

**The MCP target:** an OpenAPI 3 spec (inline payload) describing one operation: `GET /anything` at `https://httpbin.org` with `operationId: callDownstreamApi`. The Gateway exposes this as a single MCP tool by the same name.

**Outbound auth (the OBO #2 hop):** the target's `credentialProviderConfigurations` array has one entry of type `OAUTH` with:
- `providerArn` → an AgentCore Identity `CustomOauth2` credential provider configured for OBO with `grantType: "TOKEN_EXCHANGE"` (Okta's RFC 8693 flavor) and `actorTokenContent: "NONE"`.
- `grantType: "TOKEN_EXCHANGE"` — this is the Gateway-target field that tells the Gateway "do an OBO exchange before forwarding." (Same enum name as the credential provider config, different level, both required.)
- `scopes: ["downstream.access"]` — what to request from the exchange.
- `customParameters: { "subject_token_type": "urn:ietf:params:oauth:token-type:access_token" }` — RFC 8693's required parameter for Token Exchange.

When the Gateway receives a `tools/call` for `callDownstreamApi`:
1. It validates the inbound token at the MCP auth layer (`allowedAudience = [OKTA_AUDIENCE]`).
2. It calls AgentCore Identity to perform OBO #2:
   - Gateway's service role is used to call `GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token`.
   - AgentCore Identity uses **GatewayApp's** client credentials (stored on the credential provider) and POSTs a Token Exchange request to Okta with the inbound token as `subject_token`.
   - Returns `T_downstream`. `aud = OKTA_AUDIENCE`, `cid = GATEWAY_CLIENT_ID`, `sub` unchanged, `scp = [downstream.access]`.
3. It calls `GET https://httpbin.org/anything` with `Authorization: Bearer T_downstream`.
4. It maps the response back as the MCP tool result. httpbin echoes the request headers back, so the response contains the T_downstream token that was forwarded — useful for learning, not something you'd have in production.

The Gateway never exposes `T_downstream` to the agent. The agent never sees a downstream URL.

### AgentCore Identity

Holds **two** credential providers in this use case:

| Provider | Used by | OAuth client identity | What it does |
|---|---|---|---|
| `obo-uc2-okta-agent-actor` | Agent code (OBO #1) | AgentApp | Exchange `T_user` -> `T_gateway` (scp: gateway.access) |
| `obo-uc2-okta-gateway-actor` | Gateway target (OBO #2) | GatewayApp | Exchange `T_gateway` -> `T_downstream` (scp: downstream.access) |

Both providers are `CustomOauth2` with `onBehalfOfTokenExchangeConfig.grantType = TOKEN_EXCHANGE` and `tokenExchangeGrantTypeConfig.actorTokenContent = NONE`. This is **not** a built-in vendor — the built-in `OktaOauth2` vendor doesn't expose the OBO config knobs Gateway needs. Both providers store a different client secret because they authenticate as different Okta apps.

### Okta — Three app registrations

| App | Type | Purpose | Grant types | DPoP |
|---|---|---|---|---|
| **FrontendApp** | Web App (OIDC confidential) | Browser signs into this. | `authorization_code`, `refresh_token`, PKCE required | N/A |
| **AgentApp** | API Services | Middle-tier client for OBO #1. | `urn:ietf:params:oauth:grant-type:token-exchange` | **Must be OFF** |
| **GatewayApp** | API Services | Middle-tier client for OBO #2. | `urn:ietf:params:oauth:grant-type:token-exchange` | **Must be OFF** |

**Why three apps?** Each OBO hop crosses a trust surface, and each surface deserves its own client credentials. Compromising the agent's secret leaks AgentApp's identity but not GatewayApp's.

**Why DPoP OFF?** Newer Okta Integrator tenants default DPoP ON on API Services apps. AgentCore Identity does not sign DPoP proofs, so an ON setting causes OBO to fail at runtime with `invalid_dpop_proof: The DPoP proof JWT header is missing`. The automation script (`deploy/00_create_okta_apps.py`) explicitly turns it off; the manual walkthrough in `IDP_SETUP.md` covers the console step.

### Okta authorization server — Custom scopes + Access Policies

Three custom scopes on the default authorization server (or your custom one if you're not using default):

| Scope | Requested by | Present in |
|---|---|---|
| `agent.access` | FrontendApp at sign-in | `T_user` |
| `gateway.access` | AgentApp via OBO #1 | `T_gateway` |
| `downstream.access` | GatewayApp via OBO #2 | `T_downstream` |

**Why custom scopes and not `openid`?** Okta refuses `openid` on the Token Exchange grant (reason: `openid_not_allowed_token_exchange`) AND refuses other OIDC scopes (`profile`, `email`) without `openid`. Custom scopes sidestep both rules. This is also the realistic production pattern — OBO tokens are for your own resource servers, not for the IdP's identity endpoints.

Three access policies on the auth server — one per app:

| Policy | Assigned to | Allowed grant type | Allowed scopes |
|---|---|---|---|
| Frontend | FrontendApp | `authorization_code`, `refresh_token` | `openid profile email offline_access agent.access` |
| Agent OBO | AgentApp | `token-exchange` | `gateway.access` |
| Gateway OBO | GatewayApp | `token-exchange` | `downstream.access` |

All three policies must be **Active**. An Inactive policy is silently ignored during evaluation — the classic Okta trap when things fail with `Policy evaluation failed for this request`.

## The three tokens, decoded

Here's what the same user (`alice@example.com`) would have at each hop. The key lines to watch are `cid` (rotates) and `sub` (constant).

```
T_user (after sign-in, held by BFF)
  iss   : https://<OKTA_DOMAIN>/oauth2/<auth-server-id>
  aud   : api://default                        ← the auth server's audience
  cid   : <FRONTEND_CLIENT_ID>                 ← actor: the frontend
  sub   : alice@example.com                    ← who Alice is
  uid   : 00u<Alice's Okta user ID>            ← Alice's internal ID; also constant
  scp   : [openid, profile, email, agent.access]
  exp   : <timestamp>

T_gateway (after OBO #1, used by agent → Gateway)
  iss   : https://<OKTA_DOMAIN>/oauth2/<auth-server-id>
  aud   : api://default                        ← UNCHANGED — same audience, always
  cid   : <AGENT_CLIENT_ID>                    ← actor rotated: now the agent
  sub   : alice@example.com                    ← UNCHANGED — Alice is still Alice
  uid   : 00u<Alice's Okta user ID>            ← UNCHANGED
  scp   : [gateway.access]                     ← scope narrowed to what agent needs
  exp   : <timestamp>

T_downstream (after OBO #2, used by Gateway → mock API)
  iss   : https://<OKTA_DOMAIN>/oauth2/<auth-server-id>
  aud   : api://default                        ← UNCHANGED
  cid   : <GATEWAY_CLIENT_ID>                  ← actor rotated again: the gateway
  sub   : alice@example.com                    ← UNCHANGED
  uid   : 00u<Alice's Okta user ID>            ← UNCHANGED
  scp   : [downstream.access]                  ← scope narrowed again
  exp   : <timestamp>
```

Three things to notice:

1. **`sub` and `uid` are identical at all three hops.** These are the cryptographic fingerprints of "Alice." Downstream audit logs use these to attribute every request back to the originating user, regardless of which app last touched the token.
2. **`aud` stays constant.** Okta's default authorization server mints every token with the same `aud`. This is different from Entra where each app has its own audience. In Okta's model, **scope** carries the authorization signal, not audience.
3. **`cid` walks down the chain.** `frontend → agent → gateway`. This is the actor breadcrumb trail. Okta does not add a nested `act` claim to the exchanged token unless we send an `actor_token` (we intentionally don't — `actorTokenContent: NONE`).

A small helper script, `deploy/compare_obo_claims.py`, decodes all three tokens and prints them side by side. The LEARNING_GUIDE walks through the output.

## Request lifecycle — end to end

When the user types "Call the downstream API" and clicks Ask agent:

### 1. Browser → BFF
```
POST /ask
Cookie: session=…
prompt=Call+the+downstream+API
```

### 2. BFF → Runtime
```
POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<runtime-arn>/invocations?qualifier=DEFAULT
Authorization: Bearer T_user
Content-Type: application/json

{"prompt": "Call the downstream API"}
```

### 3. Runtime inbound auth
`customJWTAuthorizer` validates T_user against Okta. If valid, the agent handler receives the request with the JWT in `context.request_headers["Authorization"]`.

### 4. Inside the agent handler
```python
user_jwt = context.request_headers["Authorization"].split(" ", 1)[1]

# OBO #1
workload_token = ac_identity.get_workload_access_token_for_jwt(
    workloadName=AGENT_WORKLOAD_NAME, userToken=user_jwt
)["workloadAccessToken"]
gateway_token = ac_identity.get_resource_oauth2_token(
    workloadIdentityToken=workload_token,
    resourceCredentialProviderName=AGENT_OBO_PROVIDER_NAME,
    scopes=[GATEWAY_SCOPE],                          # gateway.access
    oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    # RFC 8693: declare what kind of token is being exchanged.
    # AgentCore Identity does NOT auto-add this for CustomOauth2 with
    # TOKEN_EXCHANGE grant — must be passed explicitly.
    customParameters={
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"
    },
    # Announce the audience of the resulting token.
    audiences=[OKTA_AUDIENCE],
)["accessToken"]

# Open MCP session to Gateway with Bearer T_gateway, call the tool, return result.
```

The agent's tool implementation just calls the MCP tool over the Gateway connection. There is no `requests.get("https://httpbin.org/...")` here.

### 5. Agent → Gateway (MCP tools/call)
```
POST https://<gateway-endpoint>/mcp
Authorization: Bearer T_gateway
Content-Type: application/json

{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "callDownstreamApi", "arguments": {}}, "id": 1}
```

### 6. Gateway inbound auth
The Gateway validates T_gateway against Okta OIDC. `aud` must match OKTA_AUDIENCE.

### 7. Gateway outbound OBO (OBO #2)
The Gateway sees the target requires `OAUTH` outbound credentials with `grantType: TOKEN_EXCHANGE`. It calls AgentCore Identity (using its service role) to perform the exchange — same two-API-call pattern as in the agent, but executed by the Gateway service:
```
POST https://<OKTA_DOMAIN>/oauth2/<auth-server-id>/v1/token
grant_type=urn:ietf:params:oauth:grant-type:token-exchange
client_id=<GATEWAY_CLIENT_ID>
client_secret=<GATEWAY_CLIENT_SECRET>            # from credential provider
subject_token=<T_gateway>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
scope=downstream.access
```

Okta returns `T_downstream`.

### 8. Gateway → mock downstream
```
GET https://httpbin.org/anything
Authorization: Bearer T_downstream
```
httpbin.org echoes the request back including the Authorization header.

### 9. Response flows back
httpbin → Gateway → MCP `tools/call` response → agent → LLM → Runtime → BFF → browser. The BFF renders `result.html` with the LLM's short answer.

## Why two providers, not one

The two credential providers exist because **they authenticate as different Okta apps**. AgentCore Identity stores the OAuth `client_id` + `client_secret` on the provider object. OBO #1 must authenticate as AgentApp (because it's the client sending the Token Exchange for `gateway.access`), and OBO #2 must authenticate as GatewayApp (because it's the client sending the Token Exchange for `downstream.access`). One provider per app.

This is also why we need three Okta apps: each OBO-exchanging client needs its own `client_id` and `client_secret`. Sharing credentials across hops would defeat the audit trail (both hops would show the same `cid` in the resulting token).

## Security notes

- **No tokens in the browser.** All four tokens (T_user, T_gateway, T_downstream, plus refresh) live server-side.
- **LLM sees no tokens.** The LLM's tool context contains the httpbin echo JSON, which does include the T_downstream token — in a production API you'd audit that carefully. For this demo the token is in the tool response but the LLM is instructed not to repeat it.
- **The Gateway's service role can read AgentCore-managed OAuth secrets.** The deploy script attaches an inline IAM policy scoped to the `bedrock-agentcore-identity!default/oauth2/*` secret prefix.
- **Compromise blast radius.** If AgentApp's secret leaks, an attacker can mint OBO tokens with `gateway.access` scope using any user token they obtain — but they still can't mint `downstream.access` tokens, because that requires GatewayApp's secret AND the Access Policy on the auth server only allows GatewayApp to receive that scope.
- **Per-user authorization at downstream.** In production, the downstream API should validate the `sub` claim on `T_downstream` to identify the user. httpbin.org doesn't validate anything, so this is left as an exercise for when you swap in your own API.
- **No `openid` on OBO tokens.** By design — Okta refuses it, and the pattern is correct anyway. The OBO tokens are for your own resource servers, not for identity endpoints.
- **All tokens share the same `aud` claim.** This is a natural consequence of Okta's default auth server. In the audience layer this means the customJwtAuthorizer alone can't tell "was this token issued for me specifically?" — you must combine `aud` with scope validation at the resource layer. For a custom auth server with multiple audiences, this changes.

## Production considerations (not in this example)

- **Retire the long-lived client secrets** on AgentApp and GatewayApp. Use federated identity approaches (e.g., Okta OAuth 2.0 for AWS with AssumeRoleWithWebIdentity) where possible.
- **Custom auth server per audience.** If you have a real production API, define a custom Okta auth server with its own audience so `aud` alone distinguishes tokens intended for it.
- **Scope validation at the resource layer.** Your downstream should validate `scp` contains `downstream.access` and reject tokens without it, even if `aud` matches.
- **DPoP or mTLS.** Both raise the bar for token theft — but you'd need an infrastructure layer that can sign DPoP proofs (not AgentCore Identity today).
- **Rotate provider secrets.** Both credential providers store client secrets that need rotation policies. Rotating is a `update-oauth2-credential-provider` call; no Runtime redeploy needed.
- **Group-based scope grants.** Instead of `EVERYONE`, restrict each policy's rule to a specific Okta group (e.g., `agentcore-uc2-users`).
