# Use Case 1 — Okta flavor

Two runnable variants of the same OBO flow (User → Frontend → Agent → Downstream API), each aimed at a different learning goal:

| Variant | When to use it | What you deploy |
|---|---|---|
| [`local/`](./local/) | Learning, debugging, CI. See the Okta OBO exchange end-to-end from a single Python script. | Nothing on AWS compute. One Okta native app, one Okta service app, one custom authorization server. Two AgentCore credential providers. |
| [`real-world/`](./real-world/) | Demo, reference, production-shape. Real browser sign-in, real Runtime, real BFF. | A FastAPI frontend, a Strands agent on AgentCore Runtime. One Okta Web App (new) + the Service App from `local/`. One AgentCore credential provider. |

## Recommended reading order

1. **Start in [`local/`](./local/)** — run `02_run_example.py` end-to-end. It narrates the OBO exchange in five chapters, prints the inbound and outbound token claims side-by-side, and finishes by comparing what changed (audience, actor, scope) vs. what stayed the same (user `sub`). You'll see what Okta's RFC 8693 OBO does without any AWS deployment.
2. **Then go to [`real-world/`](./real-world/)** to see the same mechanics running across a real distributed system (FastAPI BFF, Strands agent on AgentCore Runtime, Okta `/v1/userinfo` as the downstream call).

`local/IDP_SETUP.md` has the Okta app registration steps and the authorization-server configuration.

## How Okta's OBO differs from Entra's

The Entra example uses RFC 7523 (JWT bearer grant). Okta uses RFC 8693 (token exchange grant). Both flows accomplish the same thing — swap a user token audienced at the middle tier for a new token audienced at the downstream API while preserving user identity — but the protocol differs in two practical ways you'll see reflected in the code:

- **Exchange-time parameters matter.** Okta requires `subject_token_type` and `audience` on the exchange call itself. They're passed as `customParameters` / `audience` on `GetResourceOauth2Token`, not configured on the credential provider. (Entra infers these from the provider config.)
- **Different identity-preservation claim.** Okta preserves `sub` (the user's login, e.g. `alice@example.com`) across the exchange. The claim that rotates to identify the actor is `cid` (client ID). Entra's equivalents are `oid` (preserved) and `appid`/`azp` (rotated).

See [`content/cheatsheet_entra_vs_okta.md`](../../../content/cheatsheet_entra_vs_okta.md) for the full side-by-side comparison.
