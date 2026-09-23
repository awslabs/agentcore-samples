# Architecture — Real-world Use Case 2 (Entra)

A deeper look at the design choices, the three Entra app registrations, the two OBO exchanges, and the request lifecycle. Read after you've run the happy path end-to-end.

## What changes vs Use Case 1

UC1 puts a single OBO hop **in the agent's tool code**. UC2 adds a second OBO hop **inside the Gateway**, and removes Graph access from the agent code path entirely.

| | Use Case 1 | Use Case 2 |
|---|---|---|
| Number of OBO exchanges | 1 (in agent) | 2 (in agent + in Gateway) |
| Agent calls Graph directly? | Yes — `requests.get("https://graph.microsoft.com/v1.0/me", …)` | No — agent calls Gateway over MCP; Gateway calls Graph |
| Agent IAM permissions for OBO | `GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token` + `secretsmanager:GetSecretValue` | Same set (still does OBO #1) |
| Gateway needs IAM permissions? | n/a (no Gateway) | Yes — same set, on the Gateway service role |
| Entra app registrations | 2 (frontend + agent) | 3 (frontend + agent + gateway) |
| Combined consent chain | FrontendApp → AgentApp via `knownClientApplications` | FrontendApp → AgentApp → GatewayApp, two `knownClientApplications` links |
| Graph permission lives on... | AgentApp | GatewayApp |
| What the LLM "sees" of tokens | Nothing — tool returns parsed JSON | Nothing — MCP tool returns parsed JSON |

The headline takeaway: **the same OBO primitive composes across layers**. Once you've built one OBO hop in code, you can put another one in infrastructure, and the chain stays cryptographically traceable.

## Components

### Frontend — FastAPI BFF

Same role as in UC1. Runs the Entra auth code flow via MSAL, holds the user's access token server-side in a session, forwards prompts to the agent with that token as Bearer.

**Token it holds:** `T_user`. `aud = AgentApp's client ID`. Issued because the user signed into FrontendApp and consented to `api://AgentApp/access_as_user`.

### Agent — Strands, on AgentCore Runtime

**Inbound:** Runtime's `customJWTAuthorizer` validates `T_user` against Entra OIDC (signature, issuer, audience = AgentApp).

**Inside the handler:**
1. Read `T_user` from `context.request_headers["Authorization"]`.
2. Perform **OBO #1** via AgentCore Identity:
   - `GetWorkloadAccessTokenForJWT(workloadName=agent-workload, userToken=T_user)`
   - `GetResourceOauth2Token(provider=agent-obo-provider, oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE, scopes=["api://GatewayApp/access_as_user"])`
   - Returns `T_gateway`. `aud = GatewayApp`, `azp = AgentApp`, `oid` unchanged.
3. Open an MCP client connection to the Gateway, presenting `T_gateway` as Bearer.
4. List + invoke tools. Specifically, the agent calls the `getMyProfile` tool exposed by the Gateway target.
5. Return the tool result to the LLM. LLM composes the answer.

**What the agent code does NOT do:** call `https://graph.microsoft.com` directly. There's no `requests.get(…)` to Graph in the agent codebase.

### AgentCore Gateway — OpenAPI target → Microsoft Graph

**Inbound auth:** `customJWTAuthorizer` configured with Entra's OIDC discovery and `allowedAudience = GatewayApp's client ID`. Validates `T_gateway` on every MCP call.

**The MCP target:** an OpenAPI 3 spec (inline payload) describing one operation: `GET /v1.0/me` with `operationId: getMyProfile`. The Gateway exposes this as a single MCP tool by the same name, plus a `tools/list` endpoint.

**Outbound auth (the OBO #2 hop):** the target's `credentialProviderConfigurations` array has one entry of type `OAUTH` with:
- `providerArn` → an AgentCore Identity `CustomOauth2` credential provider configured for OBO with `grantType: "JWT_AUTHORIZATION_GRANT"` (Entra's RFC 7523 flavor).
- `grantType: "TOKEN_EXCHANGE"` — this is the Gateway-target field that tells the Gateway "do an OBO exchange before forwarding."
- `scopes: ["https://graph.microsoft.com/.default"]` — what to request from the exchange.
- `customParameters: { "requested_token_use": "on_behalf_of" }` — Entra's required extra parameter for OBO; without this the exchange silently fails.

When the Gateway receives a `tools/call` for `getMyProfile`:
1. It validates the inbound token (already done at MCP auth layer, above).
2. It calls AgentCore Identity to perform OBO #2:
   - Gateway's service role IAM identity is used to call `GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token`.
   - AgentCore Identity uses **GatewayApp's** client credentials (stored on the credential provider) and POSTs `grant_type=jwt-bearer` to Entra with the inbound token as the assertion.
   - Returns `T_graph`. `aud = https://graph.microsoft.com`, `azp = GatewayApp`, `oid` unchanged.
3. It calls `GET https://graph.microsoft.com/v1.0/me` with `Authorization: Bearer T_graph`.
4. It maps the response back as the MCP tool result.

The Gateway never exposes `T_graph` to the agent. The agent never sees a Graph URL. The Gateway boundary is opaque to the agent on both inbound and outbound sides.

### AgentCore Identity

Holds **two** credential providers in this use case:

| Provider | Used by | OAuth client identity | What it does |
|---|---|---|---|
| `obo-uc2-entra-agent-actor` | Agent code (OBO #1) | AgentApp | Exchange `T_user` (aud=AgentApp) → `T_gateway` (aud=GatewayApp) |
| `obo-uc2-entra-gateway-actor` | Gateway target (OBO #2) | GatewayApp | Exchange `T_gateway` (aud=GatewayApp) → `T_graph` (aud=Graph) |

Both providers are `CustomOauth2` with `onBehalfOfTokenExchangeConfig.grantType = JWT_AUTHORIZATION_GRANT`. This is **not** the built-in `MicrosoftOauth2` provider, even though Entra is the IdP — the OBO exchange config is only available inside `customOauth2ProviderConfig`. Both providers store a different client secret because they authenticate as different Entra apps.

### Entra ID — Three app registrations

| App | Why it exists | Permissions on it |
|---|---|---|
| **FrontendApp** | The browser signs into this. Confidential client (BFF holds the secret). | API permissions: delegated `api://AgentApp/access_as_user` |
| **AgentApp** | Audience for `T_user`. OAuth client for OBO #1. | Expose API: `access_as_user`. Authorized client app: FrontendApp. API permissions: delegated `api://GatewayApp/access_as_user` |
| **GatewayApp** | Audience for `T_gateway`. OAuth client for OBO #2. | Expose API: `access_as_user`. Authorized client app: AgentApp. API permissions: delegated Microsoft Graph `User.Read` (admin-consented) |

**The three-app chain is intentional.** Each OBO hop crosses a trust surface, and each surface deserves its own audience and its own client credentials. Compromising the agent's secret leaks AgentApp's identity but not GatewayApp's. This is the pattern you'd use in production.

## The three tokens, decoded

Here's what the same user (`alice@example.com`) would have at each hop. The key lines to watch are `aud`, `azp`, and `oid`.

```
T_user (after sign-in, held by BFF)
  iss   : https://sts.windows.net/<tenant>/
  aud   : <AgentApp client ID>
  azp   : <FrontendApp client ID>           ← actor: the frontend
  appid : <FrontendApp client ID>           ← legacy v1 form of azp
  oid   : <Alice's stable user OID>         ← who Alice is, cross-tenant
  sub   : <PPID-1>                          ← per-app pseudonym; Alice but for AgentApp
  scp   : access_as_user
  upn   : alice@example.com

T_gateway (after OBO #1, used by agent → Gateway)
  iss   : https://sts.windows.net/<tenant>/
  aud   : <GatewayApp client ID>            ← rotated audience
  azp   : <AgentApp client ID>              ← actor rotated: now the agent
  appid : <AgentApp client ID>
  oid   : <Alice's stable user OID>         ← UNCHANGED — Alice is still Alice
  sub   : <PPID-2>                          ← different PPID for GatewayApp
  scp   : access_as_user
  upn   : alice@example.com

T_graph (after OBO #2, used by Gateway → Graph)
  iss   : https://sts.windows.net/<tenant>/
  aud   : https://graph.microsoft.com       ← Graph audience
  azp   : <GatewayApp client ID>            ← actor rotated again: the gateway
  appid : <GatewayApp client ID>
  oid   : <Alice's stable user OID>         ← UNCHANGED
  sub   : <PPID-3>
  scp   : User.Read
  upn   : alice@example.com
```

Three things to notice:

1. **`oid` is identical at all three hops.** This is the cryptographic fingerprint of "Alice." Graph's audit log can use this to attribute the request all the way back to her, regardless of which app last touched the token.
2. **`sub` differs at every hop.** Entra mints a new pairwise pseudonymous identifier per audience. Don't use `sub` for cross-app correlation; use `oid`.
3. **`azp` walks down the chain.** `frontend → agent → gateway`. This is the actor breadcrumb trail. There is no nested `act` claim in Entra (unlike Okta), but the `azp` chain combined with auth logs reconstructs the same information.

A small helper script, `deploy/compare_obo_claims.py`, decodes all three tokens and prints them side by side. The LEARNING_GUIDE walks through the output.

## Request lifecycle — end to end

When the user types "What's my email?" and clicks "Ask agent":

### 1. Browser → BFF
```
POST /ask
Cookie: session=…
prompt=What+is+my+email%3F
```

### 2. BFF → Runtime A
```
POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<runtime-arn>/invocations?qualifier=DEFAULT
Authorization: Bearer T_user
Content-Type: application/json

{"prompt": "What is my email?"}
```

### 3. Runtime A inbound auth
`customJWTAuthorizer` validates T_user against Entra. If valid, the agent handler receives the request with the JWT in `context.request_headers["Authorization"]`.

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
    scopes=[GATEWAY_SCOPE],         # api://GatewayApp/access_as_user
    oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    # Required for Microsoft OBO. AgentCore Identity does NOT auto-add this
    # for CustomOauth2 providers — only the built-in MicrosoftOauth2 vendor
    # does. Missing it → Entra returns HTTP 400 on the exchange.
    customParameters={"requested_token_use": "on_behalf_of"},
)["accessToken"]

# Open MCP session to Gateway with Bearer T_gateway, call the tool, return result.
```

The agent's tool implementation just calls the MCP tool over the Gateway connection. There is no `requests.get("https://graph…")` here.

### 5. Agent → Gateway (MCP tools/call)
```
POST https://<gateway-endpoint>/mcp
Authorization: Bearer T_gateway
Content-Type: application/json

{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "getMyProfile", "arguments": {}}, "id": 1}
```

### 6. Gateway inbound auth
The Gateway validates T_gateway against Entra OIDC. `aud` must match GatewayApp.

### 7. Gateway outbound OBO (OBO #2)
The Gateway sees the target requires `OAUTH` outbound credentials with `grantType: TOKEN_EXCHANGE` and a configured `oauthCredentialProvider`. It calls AgentCore Identity (using its service role) to perform the exchange — same two-API-call pattern as in the agent, but executed by the Gateway service:
```
POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
client_id=<GatewayApp client ID>
client_secret=<GatewayApp client secret>     # from credential provider
assertion=T_gateway
scope=https://graph.microsoft.com/.default
requested_token_use=on_behalf_of             # via customParameters
```

Entra returns `T_graph`.

### 8. Gateway → Graph
```
GET https://graph.microsoft.com/v1.0/me
Authorization: Bearer T_graph
```
Graph returns the profile JSON.

### 9. Response flows back
Graph → Gateway → MCP `tools/call` response → agent → LLM → Runtime → BFF → browser. The BFF renders `result.html` with the LLM's natural-language answer.

## Why two providers, not one

The two credential providers exist because **they authenticate as different Entra apps**. AgentCore Identity stores the OAuth `client_id` + `client_secret` on the provider object. OBO #1 must authenticate as AgentApp (because it's exchanging a token audienced at AgentApp), and OBO #2 must authenticate as GatewayApp (because it's exchanging a token audienced at GatewayApp). One provider per app.

This is also why we need three Entra apps: each OBO-exchanging client needs its own `client_id` and `client_secret`. Sharing credentials across hops would defeat the audit trail.

## Security notes

- **No tokens in the browser.** All four tokens (T_user, T_gateway, T_graph, plus MSAL refresh) live server-side.
- **LLM sees no tokens.** The LLM's tool context contains parsed Graph JSON, never a raw token.
- **The Gateway's service role can read AgentCore-managed OAuth secrets.** The deploy script attaches an inline IAM policy scoped to the `bedrock-agentcore-identity!default/oauth2/*` secret prefix — same pattern as the agent's role in UC1.
- **Compromise blast radius.** If AgentApp's secret leaks, an attacker can mint OBO tokens audienced at GatewayApp using any user token they obtain — but they still can't mint Graph tokens, because that requires GatewayApp's secret.
- **Per-user authorization at Graph.** Graph's `/me` always returns the token-holder's profile (the user identified by `oid`), regardless of the actor chain.

## Production considerations (not in this example)

- **Use managed identities or workload-identity federation** to retire the long-lived client secrets on AgentApp and GatewayApp. Both the agent's role and the Gateway's role can be set up as Federated Identity Credentials on the respective Entra apps.
- **Conditional Access propagation.** If GatewayApp has a CA policy requiring MFA step-up, OBO #2 can return `interaction_required`. Surface this back to the BFF as a 401 + claims challenge so the user can re-authenticate.
- **Tighten allowed audiences.** This example accepts any token whose `aud` matches the agent's app ID. In production you might want to also restrict `azp` to FrontendApp's client ID at the Runtime layer (a small interceptor or runtime check).
- **Tighten scopes.** Use a custom `access_as_gateway` scope on GatewayApp instead of the generic `access_as_user` so the agent's request to OBO #1 is explicitly for the Gateway delegation.
- **Rotate provider secrets.** Both credential providers store client secrets that need rotation policies. Rotating a secret is a `update-oauth2-credential-provider` call; no Runtime redeploy needed.
