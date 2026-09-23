# Use Case 1 — Entra ID flavor

Two runnable variants of the same OBO flow (User → Frontend → Agent → Microsoft Graph), each aimed at a different learning goal:

| Variant | When to use it | What you deploy |
|---|---|---|
| [`local/`](./local/) | Learning, debugging, CI. See the OBO exchange end-to-end from a single Python script. | Nothing on AWS compute. One Entra app registration. Two AgentCore credential providers. |
| [`real-world/`](./real-world/) | Demo, reference, production-shape. Real browser sign-in, real Runtime, real BFF. | A FastAPI frontend, a Strands agent on AgentCore Runtime. Two Entra app registrations. One AgentCore credential provider. |

## Recommended reading order

1. **Start in [`local/`](./local/)** — run `02_run_example.py` end-to-end. It narrates the OBO exchange in five chapters, prints the inbound and outbound JWT claims side-by-side, and finishes with a real call to Microsoft Graph. You'll see what OBO does without any AWS deployment.
2. **Then go to [`real-world/`](./real-world/)** — follow `README.md` to deploy the stack, then walk through `LEARNING_GUIDE.md` to see the same mechanics across the full distributed system (inbound JWT validation by Runtime, OBO inside the handler, tokens flowing through CloudWatch logs).

Each variant has its own `IDP_SETUP.md` with the Entra app registration steps it needs (one app for `local/`, two apps for `real-world/`).

## Why two separate setups?

Different teaching goals need different abstractions:

- `local/` collapses the frontend, agent, and downstream call into a single script so you can see the whole exchange at once. One Entra app plays both the frontend-client and the agent-resource roles.
- `real-world/` splits them back out the way production systems do: a user-facing OIDC client (frontend app) distinct from the middle-tier resource (agent app), with `knownClientApplications` linking them so the user consents to both in one prompt.

The underlying AgentCore Identity calls (`GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE`) are identical in both variants. Only the surrounding plumbing changes.
