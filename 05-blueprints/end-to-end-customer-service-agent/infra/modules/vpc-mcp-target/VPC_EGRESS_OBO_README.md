# Private MCP Server with Entra ID Identity Delegation

Demonstrates how to secure a private MCP server behind an AgentCore Gateway using Microsoft Entra ID, where the end-user's identity is preserved through the entire call chain via delegated token exchange.

## What This Proves

A customer using Microsoft Entra ID as their identity provider can:
1. Authenticate once with their corporate credentials
2. Call an AgentCore Gateway that validates their identity
3. Have the gateway automatically obtain a scoped token for the downstream MCP server
4. Receive personalized responses from private customer service tools

All without exposing the MCP server to the public internet.

## System Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  End User (corporate Entra ID account)                              │
│  Signs in via MSAL device code → receives a user-scoped JWT        │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Bearer <user_jwt>
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentCore Gateway                                                  │
│  • Inbound: CUSTOM_JWT validates the user's Entra ID token          │
│  • Identity Delegation: exchanges user token for a downstream       │
│    token scoped to the MCP server app (preserves user context)      │
│  • Target: DYNAMIC discovery (no upfront tool synchronization)      │
└────────────────────────────┬────────────────────────────────────────┘
                             │ Bearer <delegated_token>
                             │ (private — AWS backbone only)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  AgentCore Runtime (MCP Server)                                     │
│  • Validates the delegated token against Entra ID                   │
│  • Forwards Authorization header to the application container       │
│  • Tools: lookup_customer, get_order_status, create_support_ticket  │
└─────────────────────────────────────────────────────────────────────┘
```

## Entra ID App Configuration

### App Registration 1: Gateway Client (`agentcore-gateway-test`)

This is the app that end-users sign in to and that the gateway uses for delegation.

| Setting | Value |
|---------|-------|
| Client ID | `<your-gateway-client-id>` |
| Authentication | Public client flows enabled, nativeclient redirect |
| Expose an API | URI: `api://5f216de3-...`, scope: `mcp_read` |
| API Permissions | MCP Server app: `mcp_invoke` (Application, consented) |
| | MCP Server app: `user_impersonation` (Delegated, consented) |
| | Microsoft Graph: `User.Read` (Delegated, consented) |

### App Registration 2: MCP Server Audience (`AgentCore - MCP Server`)

This app defines the audience and permissions that the delegated token carries.

| Setting | Value |
|---------|-------|
| Client ID | `<your-mcp-server-client-id>` |
| Expose an API | URI: `api://c01ad993-...`, scope: `user_impersonation` |
| App Roles | `mcp_invoke` (Applications only) |
| Manifest | `requestedAccessTokenVersion: 2` |

## AWS Resource Configuration

### Gateway

| Property | Value |
|----------|-------|
| Inbound Auth | CUSTOM_JWT |
| Discovery URL | `https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration` |
| Allowed Audiences | Gateway Client ID, MCP Server Client ID |
| Exception Level | DEBUG |

### Identity Provider Registration

| Property | Value |
|----------|-------|
| Vendor | CustomOauth2 |
| Discovery URL | Same as gateway |
| Client ID | Gateway Client app |
| Delegation Config | `grantType: JWT_AUTHORIZATION_GRANT` |

### Gateway Target

| Property | Value |
|----------|-------|
| Endpoint | `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<id>/invocations?qualifier=DEFAULT&accountId=<acct>` |
| Tool Discovery | DYNAMIC (tools resolved at call time, not upfront) |
| Auth Method | OAUTH with TOKEN_EXCHANGE |
| Requested Scopes | `api://<mcp-server-id>/user_impersonation` |
| Custom Params | `requested_token_use: on_behalf_of` |

### MCP Runtime

| Property | Value |
|----------|-------|
| Auth | CUSTOM_JWT (v2.0 discovery, audience = MCP Server app) |
| Header Forwarding | `requestHeaderAllowlist: ["Authorization"]` |
| Protocol | MCP |
| Network | PUBLIC (backbone-only connectivity from Gateway) |

## Gateway IAM Role Requirements

The gateway's execution role must have these permissions for the delegation flow to work:

```
bedrock-agentcore:GetWorkloadAccessToken
bedrock-agentcore:GetWorkloadAccessTokenForJWT
bedrock-agentcore:CreateWorkloadIdentity
bedrock-agentcore:GetResourceOauth2Token
bedrock-agentcore:InvokeRuntime
secretsmanager:GetSecretValue (on bedrock-agentcore* secrets)
logs:CreateLogGroup/Stream, PutLogEvents
```

Resource ARNs must include `workload-identity-directory/default/*` and `token-vault/default/*`.

## Deployment Steps

```bash
# 1. Deploy MCP server to AgentCore Runtime
cd mcp-server && python provision_runtime.py

# 2. Register the identity provider
python setup/register_identity_provider.py --client-secret <secret>

# 3. Deploy the gateway (Terraform)
terraform apply

# 4. Wire the gateway to the runtime
python setup/wire_gateway_to_runtime.py \
  --gateway-id <from terraform> \
  --runtime-id <from step 1> \
  --provider-arn <from step 2>
```

## Validation

```bash
python setup/validate_end_to_end.py --interactive
```

## Challenges Encountered

| Problem | Resolution |
|---------|-----------|
| Target fails during creation with auth error | Tool discovery uses client_credentials internally — set discovery mode to DYNAMIC |
| Delegation exchange returns scope/audience error | User token must have `aud` matching the gateway client app ID |
| Runtime rejects the delegated token | Match the discovery URL version to the app manifest's `requestedAccessTokenVersion` |
| Runtime strips Authorization header | Add `requestHeaderAllowlist: ["Authorization"]` to the runtime configuration |
| Gateway role denied during token exchange | Add all four resource ARN patterns to the `GetResourceOauth2Token` statement |
| `userId cannot be null` on the runtime | Runtime needs a JWT authorizer to derive user identity — cannot run without one |
