# ADR-0010: Cognito Secret via AgentCore Identity Token Vault

**Status:** Accepted (supersedes earlier CDK injection approach)  
**Date:** 2025-06-24

## Context

The AgentCore Runtime uses Cognito `client_credentials` to obtain a JWT for authenticating outbound calls to the MCP Gateway. The Cognito client secret must be stored securely and accessible to the Runtime at invocation time.

An earlier version of this sample injected the secret directly as a Runtime environment variable via CDK's `unsafe_unwrap()`. This was simple but exposed the secret in CloudFormation templates and environment variable listings.

## Decision

Register the Cognito client secret in the **AgentCore Identity token vault** using `agentcore add credential`. At runtime, the `@requires_access_token` decorator fetches tokens from the vault — no secret appears in env vars, CDK templates, or code.

## Reasoning

AgentCore Identity is the purpose-built credential management service for agents. Using it:
- **Eliminates secret exposure** — the client secret lives only in the Secrets Manager-backed token vault, not in CloudFormation or environment variables
- **Demonstrates the production pattern** — `@requires_access_token` is the recommended decorator for Gateway auth, and using Identity shows the full intended workflow
- **Handles token lifecycle** — Identity manages token acquisition, caching, and refresh automatically
- **Stays educational** — the `agentcore add credential` CLI command is one line in the deploy script, keeping the sample approachable

## Alternatives Considered

- **CDK `unsafe_unwrap()` to env var (previous approach):** Simpler to wire up but exposes the secret in CloudFormation templates, `aws cloudformation describe-stacks` output, and console env var listings. Acceptable for a learning sample in a personal account, but contradicts the security posture AgentCore Identity is designed to provide.
- **AWS Secrets Manager (manual):** Production-ready with rotation, but requires custom SDK calls in the Runtime. AgentCore Identity wraps Secrets Manager with agent-aware semantics (workload identity + token vault), so using it directly is redundant.
- **SSM Parameter Store (SecureString):** Simpler than raw Secrets Manager but misses the `@requires_access_token` decorator integration.

## Consequences

- `deploy.sh` runs `agentcore add credential --name cognito-gateway-m2m --type oauth ...` to register the secret once.
- The Runtime receives only the **credential provider name** (`AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER=cognito-gateway-m2m`) as an env var — never the secret itself.
- The `@requires_access_token(provider_name="cognito-gateway-m2m", auth_flow="M2M")` decorator handles the full token lifecycle.
- The Runtime's IAM role needs `bedrock-agentcore:GetResourceOauth2Token` and `secretsmanager:GetSecretValue` permissions on the token vault resources (granted by CDK).
- If the Cognito pool is deleted and recreated, `agentcore add credential` must be re-run (the deploy script handles this idempotently).
