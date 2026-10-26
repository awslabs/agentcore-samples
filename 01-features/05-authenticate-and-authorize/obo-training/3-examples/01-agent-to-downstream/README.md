# Use Case 1 — Agent on Runtime → Downstream API (same IdP)

**Scenario:** A user signs into a frontend. The frontend invokes an agent on AgentCore Runtime. The agent — acting on the user's behalf — calls a downstream MCP server or REST API that is protected by the **same identity provider** as the frontend.

```
👤 User → 🖥️  Frontend → 🤖 Agent (on AgentCore Runtime) → 🎯 Downstream API
                                       │
                                       └── OBO exchange via AgentCore Identity ←→ 🔑 IdP
```

## What this example demonstrates

- **Inbound OAuth** — the agent runtime validates the user's JWT on incoming invocations.
- **Outbound OBO exchange** — the agent swaps the inbound user JWT for a new token scoped to the downstream API, using AgentCore Identity's `ON_BEHALF_OF_TOKEN_EXCHANGE` flow.
- **Downstream call** — the agent calls the downstream API with the new, properly-audienced token.

## Flavors

- [`entra/`](./entra/) — Microsoft Entra ID (JWT bearer grant, RFC 7523). Includes both `local/` (interactive script) and `real-world/` (deployed BFF + AgentCore Runtime agent).
- [`okta/`](./okta/) — Okta (token exchange grant, RFC 8693). Includes both `local/` (interactive script) and `real-world/` (deployed BFF + AgentCore Runtime agent, calls Okta's `/v1/userinfo`).

Each flavor has its own setup steps, credential provider config, and runnable scripts. They share the same overall sequence; only the IdP-specific protocol details differ.

## Two variants per flavor

Inside each IdP folder there are (or will be) two variants:

- **`local/`** — a single Python script that simulates the full flow from your laptop. Best for learning and debugging.
- **`real-world/`** — a production-shaped deployment: FastAPI frontend (BFF), Strands agent deployed on AgentCore Runtime, real browser sign-in. Best for demos and reference.

Start with `local/` to understand OBO, then look at `real-world/` to see how it fits into a real application.

## Simplification: the local variant vs real-world

The `local/` variant in each flavor simulates the full flow without deploying anything to AWS compute. The `02_run_example.py` script plays every role:

1. Runs a local 3LO to mint a realistic user JWT (this is the piece that, in production, a real frontend hands to the agent).
2. Calls AgentCore Identity exactly as an agent handler would — `GetWorkloadAccessTokenForJWT` → `GetResourceOauth2Token(ON_BEHALF_OF_TOKEN_EXCHANGE)`.
3. Uses the resulting OBO token to call a downstream API (Microsoft Graph for Entra; a placeholder API for Okta).

This gives you a faithful reproduction of the OBO exchange that you can run locally without deploying any AWS compute.

The `real-world/` variant layers in the real components: a FastAPI backend-for-frontend, a Strands agent deployed on AgentCore Runtime, and the IdP's standard auth-code flow for browser sign-in. Both flavors are available: [`entra/real-world/`](./entra/real-world/) calls Microsoft Graph, and [`okta/real-world/`](./okta/real-world/) calls Okta's `/v1/userinfo`.
