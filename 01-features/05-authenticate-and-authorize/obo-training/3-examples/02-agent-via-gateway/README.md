# Use Case 2 — User → BFF → Agent on Runtime → AgentCore Gateway → REST API

**Scenario:** A user signs into a frontend. The frontend invokes an agent on AgentCore Runtime. The agent — acting on the user's behalf — calls an AgentCore Gateway target that, in turn, calls a downstream REST API. **Two OBO exchanges happen in this chain**, one in the agent and one in the Gateway, and the user identity is preserved end-to-end.

```
👤 User → 🖥️  Frontend → 🤖 Agent (AgentCore Runtime)
                              │
                              └── OBO #1 via AgentCore Identity ←→ 🔑 IdP
                              │     (T_user → T_gateway, audience rotates: agent → gateway)
                              ▼
                          🛡  AgentCore Gateway
                              │
                              └── OBO #2 via AgentCore Identity ←→ 🔑 IdP
                              │     (T_gateway → T_downstream, audience rotates: gateway → API)
                              ▼
                          🎯 Downstream REST API
```

The single most important property of this chain: at every hop the **user's stable identifier** (`oid` in Entra, `sub` + `uid` in Okta) stays the same, while the **actor** (`azp` / `appid` in Entra, `cid` in Okta) rotates `frontend → agent → gateway`. The downstream API can both authorize as the user and audit the full delegation trail.

## What this example demonstrates beyond Use Case 1

UC1 already showed one OBO hop. UC2 layers in:

- **A second OBO hop** at infrastructure level. The Gateway exchanges its inbound token for a downstream-audienced token without the agent code touching it.
- **The Gateway's outbound OAuth credential provider model.** A `CustomOauth2` provider configured for OBO is attached to a Gateway target; the Gateway calls AgentCore Identity for each tool call.
- **Identity propagation through three audiences.** The `oid` claim is the seam — UC2 is built around that observation.

## Flavors

- [`entra/`](./entra/) — Microsoft Entra ID (RFC 7523 JWT bearer). Downstream: Microsoft Graph `/me`. Status: **complete**.
- [`okta/`](./okta/) — Okta (RFC 8693 token exchange). Downstream: mock echo API (httpbin.org). Status: **complete**.

Each flavor is a fully self-contained `real-world/` deployment. There is no `local/` variant for this use case — the Gateway is a deployed AWS resource and there's no meaningful way to simulate the second OBO hop without it.

## Reading order

Pick one flavor to follow end-to-end first, then compare with the other. Both are structured identically.

**Entra path:**
1. **[`entra/real-world/README.md`](./entra/real-world/README.md)** — setup checklist. Follow it to get the stack deployed.
2. **[`entra/real-world/ARCHITECTURE.md`](./entra/real-world/ARCHITECTURE.md)** — design choices, request lifecycle, three tokens decoded.
3. **[`entra/real-world/LEARNING_GUIDE.md`](./entra/real-world/LEARNING_GUIDE.md)** — six chapters walking through the two-OBO mechanics in the logs and tokens.

**Okta path:**
1. **[`okta/real-world/README.md`](./okta/real-world/README.md)** — setup checklist.
2. **[`okta/real-world/ARCHITECTURE.md`](./okta/real-world/ARCHITECTURE.md)** — design + request lifecycle + three tokens.
3. **[`okta/real-world/LEARNING_GUIDE.md`](./okta/real-world/LEARNING_GUIDE.md)** — same six-chapter format.

## Key differences between the two flavors

Same chain shape, different OBO protocol on the wire:

| Aspect | Entra | Okta |
|---|---|---|
| OBO protocol | RFC 7523 JWT Bearer grant | RFC 8693 Token Exchange grant |
| Credential provider grant | `JWT_AUTHORIZATION_GRANT` | `TOKEN_EXCHANGE` + `actorTokenContent: NONE` |
| Custom parameters on exchange | `{"requested_token_use": "on_behalf_of"}` | `{"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"}` |
| Additional args | none | `audiences=[OKTA_AUDIENCE]` |
| Audience across the chain | Rotates (AgentApp → GatewayApp → Graph) | Stays constant (`api://default`) |
| Actor claim (rotates) | `azp` / `appid` | `cid` |
| Identity claim (constant) | `oid` | `sub` + `uid` |
| Combined consent model | `knownClientApplications` chain on apps | Access Policies on the auth server |
| Downstream in the example | Microsoft Graph `/me` (real API) | httpbin.org/anything (echo, mock) |
| Frontend OAuth lib | MSAL | authlib |
