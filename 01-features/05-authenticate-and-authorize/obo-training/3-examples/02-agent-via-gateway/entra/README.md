# Use Case 2 — Entra ID flavor

OBO chain: **User → Frontend → Agent on Runtime → AgentCore Gateway → Microsoft Graph `/me`**, with two OBO exchanges using Entra's RFC 7523 JWT-bearer flow.

```
👤 User
  ↓ sign-in (browser, MSAL)
🖥️  FastAPI BFF
  ↓ Bearer T_user  (aud = AgentApp, azp = FrontendApp)
🤖 Strands agent on AgentCore Runtime
  ↓ OBO #1  (RFC 7523 jwt-bearer to Entra)
  ↓ Bearer T_gateway  (aud = GatewayApp, azp = AgentApp, oid preserved)
🛡  AgentCore Gateway  (OpenAPI target → Microsoft Graph)
  ↓ OBO #2  (RFC 7523 jwt-bearer to Entra, executed by Gateway)
  ↓ Bearer T_graph  (aud = https://graph.microsoft.com, azp = GatewayApp, oid preserved)
🎯 Microsoft Graph /me
```

Three Entra app registrations involved (none reused from Use Case 1):

| App | Role | Has secret? | OAuth permissions |
|---|---|---|---|
| `agentcore-obo-uc2-frontend` | User-facing OIDC client (BFF signs in here) | yes | Delegated: `api://AgentApp/access_as_user` |
| `agentcore-obo-uc2-agent` | Audience for Runtime; OBO #1 client | yes | Delegated: `api://GatewayApp/access_as_user` |
| `agentcore-obo-uc2-gateway` | Audience for Gateway; OBO #2 client | yes | Delegated: Microsoft Graph `User.Read` |

Combined consent works through `knownClientApplications`:

- AgentApp lists FrontendApp as a known client → user signs in to FrontendApp once and the consent prompt covers AgentApp's `access_as_user` scope.
- GatewayApp lists AgentApp as a known client → the same single sign-in covers GatewayApp's `access_as_user` and Graph `User.Read` scopes too.

End result: one consent prompt at sign-in covers all four scopes the chain needs. Both OBO exchanges then run without any further user interaction.

## Variants

- [`real-world/`](./real-world/) — FastAPI BFF, Strands agent on AgentCore Runtime, AgentCore Gateway with an OpenAPI target. **In progress.**

There is no `local/` variant for UC2 — the second OBO hop runs inside Gateway, which is a deployed AWS resource.
