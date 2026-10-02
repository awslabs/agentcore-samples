# Databricks Per-User Delegation with AgentCore Gateway Interceptor

> **AgentCore Gateway now supports the `TOKEN_EXCHANGE` grant type natively**, which reaches the same
> outcome without an interceptor Lambda. Where the native path is available to your AWS account and
> Region, prefer [`databricks-dbsql-native-obo`](../databricks-dbsql-native-obo). This sample remains
> the working approach where it is not.

Per-user identity propagation from Entra ID to Databricks via AgentCore Gateway REQUEST interceptor and RFC 8693 OAuth Token Exchange.

## Overview

The [existing Databricks DBSQL sample](../databricks-dbsql-agentcore-gateway) demonstrates M2M (service principal) authentication. This sample extends it with **per-user delegation** — each request carries the individual user's identity through to Databricks, enabling Unity Catalog to enforce per-user ACL.

### The Problem

Databricks on AWS requires [RFC 8693 Token Exchange](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-exchange) to convert an external JWT into a Databricks user token. When this sample was written, Gateway supported only `CLIENT_CREDENTIALS` (M2M) and `AUTHORIZATION_CODE` for outbound auth, so the exchange had to happen outside it. Gateway now also supports `TOKEN_EXCHANGE` natively — see the note above.

### The Solution

A Gateway **REQUEST interceptor** (Lambda, ~30 lines) bridges the gap:

1. Gateway validates the inbound Entra ID JWT
2. Interceptor reads the `Authorization` header (Entra JWT)
3. Interceptor calls Databricks `/oidc/v1/token` with RFC 8693 token exchange
4. Databricks returns a user-scoped token
5. Interceptor overrides the outbound `Authorization` header
6. Gateway forwards the MCP request to Databricks with the user's token
7. `current_user()` returns the actual user's email, not a service principal

## Prerequisites

1. AWS credentials configured with permissions for AgentCore, IAM, Lambda, Cognito
2. Databricks workspace on AWS with Unity Catalog enabled
3. Databricks service principal with OAuth secret (for tool sync fallback)
4. Microsoft Entra ID tenant with an app registration
5. Databricks [federation policy](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy) trusting the Entra tenant
6. Databricks user matching the Entra `email` claim (via SCIM or manual)
7. Python 3.10+

### Databricks Federation Policy

Create in the Databricks account console (Settings → Authentication → Federation policies):

| Field | Value |
|---|---|
| Issuer | `https://sts.windows.net/<entra-tenant-id>/` (trailing slash required) |
| Audiences | `<entra-app-client-id>` |
| Subject claim | `email` |

> **Important:** The issuer must match the `iss` claim in the Entra JWT exactly. Device code flow with `/.default` scope returns v1.0 tokens with issuer `https://sts.windows.net/<tenant-id>/`, not `https://login.microsoftonline.com/<tenant-id>/v2.0`.

## Getting Started

The notebook covers:

1. Configure Databricks and Entra ID credentials
2. Create Gateway with Entra ID as inbound JWT authorizer
3. Create OAuth2 credential provider for Databricks (M2M fallback for tool sync)
4. Deploy the interceptor Lambda (RFC 8693 token exchange)
5. Add Databricks DBSQL MCP server as gateway target
6. Test: `SELECT current_user()` returns the Entra user's email
7. Verify via CloudWatch logs (step-by-step interceptor trace)

## Architecture

### Under the Hood: Control Plane vs Data Plane

Two separate token flows. The SP token discovers tools. The user token executes queries.

#### Control Plane (setup time, runs once)

```
Admin                          AgentCore                    Databricks
  │                               │                            │
  │── create_gateway_target ─────▶│                            │
  │   type: mcpServer             │                            │
  │   creds: CLIENT_CREDENTIALS   │                            │
  │   endpoint: /api/2.0/mcp/sql  │                            │
  │                               │                            │
  │── synchronize_gateway_targets▶│                            │
  │                               │── POST /oidc/v1/token ────▶│
  │                               │   grant_type=              │
  │                               │   client_credentials       │
  │                               │   client_id=<SP>           │
  │                               │   client_secret=<secret>   │
  │                               │◀─ { access_token: SP_TOK }─│
  │                               │                            │
  │                               │── POST /api/2.0/mcp/sql ──▶│
  │                               │   Authorization: SP_TOK    │
  │                               │   method: tools/list       │
  │                               │◀─ tools: [execute_sql,    ─│
  │                               │   execute_sql_read_only,   │
  │                               │   poll_sql_result]         │
  │◀─ tools synced ────────────── │                            │

  SP token used. No interceptor. Gateway discovers tools from
  Databricks managed MCP using the credential provider.
```

#### Data Plane (every agent request)

```
Agent          Gateway         Interceptor       Databricks       Databricks
(Entra JWT)    (validates)     (Lambda)          OIDC             MCP
  │               │               │               │               │
  │── POST /mcp ─▶│               │               │               │
  │  Auth: Bearer │               │               │               │
  │  <ENTRA_JWT>  │               │               │               │
  │  body:        │               │               │               │
  │   tools/call  │               │               │               │
  │   execute_sql │               │               │               │
  │               │               │               │               │
  │               │── VALIDATE    │               │               │
  │               │  sig/exp/     │               │               │
  │               │  aud/iss ✅   │               │               │
  │               │               │               │               │
  │               │  Gateway would normally fetch SP token here   │
  │               │  from credential provider (CLIENT_CREDENTIALS)│
  │               │  BUT interceptor runs first ↓                 │
  │               │               │               │               │
  │               │── invoke ────▶│               │               │
  │               │  headers:     │               │               │
  │               │   Auth: Bearer│               │               │
  │               │   <ENTRA_JWT> │               │               │
  │               │  body:        │               │               │
  │               │   tools/call  │               │               │
  │               │               │               │               │
  │               │               │── POST ──────▶│               │
  │               │               │  /oidc/v1/    │               │
  │               │               │  token        │               │
  │               │               │  grant_type=  │               │
  │               │               │  token-       │               │
  │               │               │  exchange     │               │
  │               │               │  subject_token│               │
  │               │               │  =<ENTRA_JWT> │               │
  │               │               │               │               │
  │               │               │◀─ {          ─│               │
  │               │               │  access_token:│               │
  │               │               │  <USER_TOK> } │               │
  │               │               │               │               │
  │               │◀─ return ──── │               │               │
  │               │  headers:     │               │               │
  │               │   Auth: Bearer│               │               │
  │               │   <USER_TOK>  │  ← OVERRIDES SP TOKEN         │
  │               │  body:        │               │               │
  │               │   tools/call  │               │               │
  │               │               │               │               │
  │               │── POST /api/2.0/mcp/sql ─────────────────────▶│
  │               │  Authorization: Bearer <USER_TOK>             │
  │               │  method: tools/call                           │
  │               │  name: execute_sql                            │
  │               │  arguments: {query: "SELECT ..."}             │
  │               │               │               │               │
  │               │               │               │  Resolves     │
  │               │               │               │  USER_TOK to  │
  │               │               │               │  user@co.com  │
  │               │               │               │               │
  │               │◀─ SQL results ────────────────────────────────│
  │◀─ results ─── │               │               │               │

  USER token used. SP token never sent on data plane.
  Interceptor swapped it before the request left Gateway.
```

### Control Plane vs Data Plane

| | Control Plane (tool sync) | Data Plane (every request) |
|---|---|---|
| When | `synchronizeGatewayTargets` | Agent calls `tools/call` |
| Token | SP via credential provider (M2M) | User via interceptor (RFC 8693) |
| Identity | Service principal | Actual user |
| Interceptor | Not involved | Reads JWT, exchanges, overrides header |

### Security

| Layer | What | How |
|---|---|---|
| Inbound auth | Gateway validates Entra JWT | Signature, expiry, audience, issuer |
| Tool-level ACL | Cedar policies (optional) | Permit/deny per caller per tool |
| Identity swap | Interceptor Lambda | RFC 8693 token exchange |
| Data-level ACL | Unity Catalog | Per-user permissions |
| Secrets | SP creds in Secrets Manager | Encrypted at rest, auto-refreshed |
| User token | Exists only in Lambda memory | Never stored, never logged, never returned |

## Gotchas

1. **Token version matters.** Entra v1.0 tokens use `iss: sts.windows.net`. Use v1.0 discovery URL for Gateway. Use `sts.windows.net` issuer for the Databricks federation policy.

2. **`allowedClients` breaks v1.0 tokens.** Entra v1.0 uses `appid` not `client_id`. Omit `allowedClients` from Gateway authorizer, use `allowedAudience` only.

3. **App tokens can't do per-user.** `client_credentials` tokens have no `email` claim. Per-user requires a user token (device code, auth code, or SSO).

4. **Tool sync needs SP.** `synchronizeGatewayTargets` uses the M2M credential provider. The interceptor only overrides auth on `tools/call`.

5. **Databricks user must exist.** The `email` in the Entra JWT must match a Databricks user with workspace access and SQL warehouse CAN USE permission.

## Resources

- [Databricks managed MCP servers](https://docs.databricks.com/aws/en/generative-ai/mcp/managed-mcp)
- [Databricks OAuth token federation (RFC 8693)](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-exchange)
- [Databricks federation policy configuration](https://docs.databricks.com/aws/en/dev-tools/auth/oauth-federation-policy)
- [AgentCore Gateway interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
- [AgentCore Gateway header propagation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)
- [Microsoft Entra ID as AgentCore IdP](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idp-microsoft.html)
- [Existing M2M sample](../databricks-dbsql-agentcore-gateway)
