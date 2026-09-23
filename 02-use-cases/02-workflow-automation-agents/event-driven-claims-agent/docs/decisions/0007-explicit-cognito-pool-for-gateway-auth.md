# ADR-0007: One Shared Cognito Pool for Runtime + Gateway

**Status:** Accepted  
**Date:** 2025-06-17

## Context

AgentCore is flexible about identity: the Gateway can provision a Cognito pool for you, or you can bring your own. This sample needs M2M (`client_credentials`) auth in two places — callers reaching the Runtime, and the Runtime reaching the Gateway — so it's a good opportunity to show a clean, unified identity setup.

## Decision

Create one explicit `cognito.UserPool` in the infra construct and use it for both the Runtime and the Gateway's CUSTOM_JWT authorizer, with a single M2M app client.

## Reasoning

Sharing one pool gives the sample a single, easy-to-follow identity model:

- **One M2M client, one token.** The same `client_credentials` token works for both hops, so the auth flow is easy to trace end to end.
- **Fewer moving parts.** One user pool, one resource server, one app client — less to reason about when learning the pattern.
- **Full control.** Defining the pool explicitly lets the sample wire its client ID into the Gateway authorizer (`allowedClients`) and inject the same credentials into the Runtime, all from one place in `infra-construct.ts`.

This showcases that AgentCore Gateway accepts any standards-compliant OIDC provider via its JWT authorizer — you're free to plug in your organization's existing identity provider the same way.

## Alternatives Considered

- **Let the Gateway provision its own pool:** Perfectly fine for a standalone gateway. For this sample, a shared pool keeps the Runtime and Gateway on one identity and one token, which is simpler to teach.

## Consequences

The pool's Cognito domain uses the prefix `claims-agent-{account}`, which is unique per account. To run two copies of the sample in the same account, give the second one a different domain prefix in `infra-construct.ts`.
