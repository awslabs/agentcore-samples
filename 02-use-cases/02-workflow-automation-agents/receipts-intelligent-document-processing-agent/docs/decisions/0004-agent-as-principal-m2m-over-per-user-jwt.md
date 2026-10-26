# ADR-0004: Agent-as-Principal M2M Over Per-User JWT

**Status:** Accepted
**Date:** 2026-06-24

## Context

The Gateway needs an inbound authorizer, and the agent needs credentials to call it. The obvious instinct is a per-user JWT — the logged-in user's identity flows through to the tool calls. But this is an **event-driven** pipeline: a receipt lands in S3 and a trigger fires. There is no logged-in user at processing time.

## Decision

The agent authenticates as **itself** using a Cognito machine-to-machine (M2M) `client_credentials` flow. The Gateway's authorizer is `CUSTOM_JWT` validating that M2M token. Per-user *data* separation lives at the data layer, not in the token. (This reverses an early design assumption and matches the claims sample's agent-as-principal model, ADR-0004.)

## Reasoning

Per-user JWT was fiction for this front door: when the trigger Lambda invokes the Runtime, whose JWT would it present? There's no interactive session. M2M is the honest model — the agent is a service principal acting on the system's behalf. The user a given receipt belongs to is carried as data (`user_id` in the payload, derived from the S3 key), and every tool only ever touches the `userId` it was handed. The Expenses table is partitioned by `userId`, so one user's rows are physically separated from another's.

## Alternatives Considered

- **Per-user JWT:** no authenticated user exists at processing time; rejected as unrealizable for an event-driven trigger.
- **IAM/SigV4 to the Gateway:** works for AWS-internal callers, but M2M Cognito demonstrates the OAuth2 pattern external integrations actually use, and keeps one identity model across the Gateway hop.

## Consequences

The Runtime holds M2M client credentials (injected as env via the seam — see [ADR-0014](0014-cognito-secret-via-cdk-injection.md)) and mints a token per Gateway session. Authorization on *what* a tool may do is Cedar's job, gating on the tool input (see [ADR-0012](0012-cedar-on-tool-input.md)) — not on caller identity. If this sample grew an interactive UI, that surface would add its own per-user auth in front; the agent-as-principal model for the automated path stays as is.
