# ADR-0004: Hybrid Auth — SigV4 Inbound + Cognito M2M Outbound

**Status:** Accepted  
**Date:** 2025-06-24

## Context

The system has two authentication boundaries:
1. **Inbound to Runtime** — Trigger Lambda and test scripts invoking the AgentCore Runtime
2. **Outbound from Runtime to Gateway** — the Runtime calling Lambda tools via the MCP Gateway

## Decision

Use a **hybrid auth model** with the simplest appropriate mechanism for each boundary:

- **Inbound (callers → Runtime):** AWS_IAM (SigV4). The Trigger Lambda's execution role and users' IAM credentials sign requests to the Runtime endpoint. CDK grants permission via `runtime.grantInvoke(triggerFn)`.
- **Outbound (Runtime → MCP Gateway):** Cognito M2M JWT (`client_credentials` flow). The Runtime obtains a JWT from the Cognito User Pool and sends it as `Authorization: Bearer {token}`. The Gateway validates via its CUSTOM_JWT authorizer (Cognito OIDC discovery URL).

## Reasoning

### Why SigV4 for inbound

The Trigger Lambda runs in the same AWS account — IAM is the natural, zero-config auth mechanism. Using `runtime.grantInvoke()` is a single CDK line with no token management, no secret rotation, and no token endpoint latency. The test scripts also benefit: they use the user's existing AWS credentials (no separate Cognito client setup required).

### Why Cognito JWT for outbound (Runtime → Gateway)

The MCP Gateway's CUSTOM_JWT authorizer demonstrates a **portable, real-world auth pattern** that generalizes beyond AWS-internal callers. This shows how the agent authenticates as a principal to external APIs and tool registries — the same `client_credentials` flow any external app, partner integration, or CI system would use to reach an MCP Gateway.

It also demonstrates the **agent-as-principal** pattern: the Runtime itself obtains a token and presents it to the Gateway, establishing the agent's identity for Cedar policy evaluation.

## Alternatives Considered

- **SigV4 for both paths (AWS_IAM on Gateway):** Simpler overall, but doesn't demonstrate the external integration pattern. Limits teaching value for real-world scenarios where the Gateway serves non-AWS callers.
- **Cognito JWT for both paths:** Uses the same auth mechanism everywhere, but adds unnecessary token management overhead for same-account Lambda → Runtime calls where IAM credentials are already available.
- **API Key (x-api-key):** Simpler than JWT but less secure (no rotation, no expiry, no scoping via OAuth scopes).

## Consequences

- Trigger Lambda and test scripts use standard AWS credential chain — no Cognito setup needed for callers.
- The Runtime → Gateway path uses `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` — secrets live in the AgentCore Identity vault, never in env vars or CloudFormation.
- The deploy script (`deploy.sh`) handles Cognito provisioning interactively and registers the credential via `agentcore add credential`.
- CDK only receives the credential provider name (`AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER`) — no client secrets flow through infrastructure-as-code.
