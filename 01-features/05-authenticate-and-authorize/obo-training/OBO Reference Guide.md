# AgentCore Identity: On-Behalf-Of (OBO) Token Exchange — Reference Guide

## Table of Contents

1. [What is OBO Token Exchange?](#what-is-obo-token-exchange)
2. [Why OBO Matters for AI Agents](#why-obo-matters-for-ai-agents)
3. [How OBO Works with AgentCore Identity](#how-obo-works-with-agentcore-identity)
4. [Supported Grant Types](#supported-grant-types)
5. [actorTokenContent Options](#actortokencontent-options)
6. [Configuration Examples](#configuration-examples)
7. [Runtime Usage](#runtime-usage)
8. [Built-in Provider Support](#built-in-provider-support)
9. [Known Limitations](#known-limitations)
10. [Runnable Examples](#runnable-examples)
11. [References](#references)

---

## What is OBO Token Exchange?

On-Behalf-Of (OBO) token exchange allows an intermediary service (like an AI agent or MCP server) to exchange an inbound user access token for a new, scoped token targeting a different downstream resource. The key properties:

- The **original user's identity** is preserved in the new token
- The **agent's identity** can optionally be included (as the "actor")
- The downstream service can enforce **fine-grained, zero-trust authorization** knowing both who the user is and who is acting for them
- No additional user consent flows are needed

### Without OBO:
```
User → Agent → Downstream API (using user's original token? agent's own token?)
                               ❌ Original token may not be scoped for this API
                               ❌ Agent's own token loses user context
```

### With OBO:
```
User → Agent → AgentCore Identity → IdP Token Endpoint → New scoped token
                                                              ↓
               Agent → Downstream API (new token = user identity + agent identity + correct scope) ✅
```

---

## Why OBO Matters for AI Agents

AI agents are different from traditional microservices:

| Challenge | Without OBO | With OBO |
|-----------|-------------|----------|
| User identity at downstream | Lost (agent uses its own creds) | Preserved (token carries user identity) |
| Least-privilege access | Agent has broad permissions | Token scoped to specific downstream resource |
| Audit trail | Actions attributed to agent | Actions attributed to user via agent |
| Consent management | User must consent to each downstream service | Single consent, AgentCore handles delegation |
| Confused deputy protection | Risk of privilege escalation | Each hop has explicit identity context |

---

## How OBO Works with AgentCore Identity

### End-to-End Flow

```
┌──────────┐         ┌─────────────┐         ┌──────────────────┐         ┌──────────────┐
│   User   │──①──►   │   Agent /   │──③──►   │   AgentCore      │──⑤──►   │  IdP / Token │
│(Entra ID)│ login   │  MCP Server │ WAT     │   Identity       │ exchange │  Endpoint    │
└──────────┘         └─────────────┘         └──────────────────┘         └──────────────┘
                           │                                                      │
                           │                         ⑦ new token returned         │
                           │◄─────────────────────────────────────────────────────┘
                           │
                           │──⑧──► Downstream Service (Databricks, Graph API, etc.)
                           │        using the new scoped token
```

### Step-by-Step:

**① User authenticates with IdP (e.g., Entra ID)**
- User signs in and receives an access token (JWT)
- This token is scoped to the agent's application/audience
- Example: token has `aud: "agent-app-client-id"`

**② Agent receives the inbound token**
- The agent or MCP server receives the user's JWT as proof of identity
- This token cannot be used directly against downstream services (wrong audience/scope)

**③ Agent calls `GetWorkloadAccessTokenForJWT`**
- Converts the inbound JWT into an AgentCore workload access token
- This internal token carries the user's identity as the subject

```bash
aws bedrock-agentcore get-workload-access-token-for-jwt \
    --workload-name my-agent-workload \
    --user-token "<inbound-jwt>"

# Returns:
{ "workloadAccessToken": "<wat-token>" }
```

**④ Agent calls `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE`**
- Tells AgentCore Identity: "Exchange this user's token for a token I can use with the downstream service"

```bash
aws bedrock-agentcore get-resource-oauth2-token \
    --resource-credential-provider-name my-obo-provider \
    --oauth2-flow ON_BEHALF_OF_TOKEN_EXCHANGE \
    --scopes "downstream-scope" \
    --workload-identity-token "<wat-token>"
```

**⑤ AgentCore Identity constructs the token exchange request**
- Based on the configured `grantType`, AgentCore builds the appropriate request
- Sends it to the configured token endpoint
- Includes client authentication (always)
- Includes actor token (if configured)

**⑥ Token endpoint validates and responds**
- Validates the subject token (user identity)
- Validates client authentication (agent identity)
- Validates actor token (if present)
- Issues a new access token scoped to the downstream resource

**⑦ AgentCore returns the new token to the agent**

**⑧ Agent calls downstream service**
- Uses the newly exchanged token as a Bearer token
- Downstream service sees: user identity + proper scope + agent identity

---

## Supported Grant Types

AgentCore Identity supports two standards for OBO token exchange:

### 1. TOKEN_EXCHANGE (RFC 8693)

The standard OAuth 2.0 Token Exchange protocol.

**Wire format sent to token endpoint:**

| Parameter | Value |
|-----------|-------|
| `grant_type` | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | Inbound user JWT |
| `subject_token_type` | `urn:ietf:params:oauth:token-type:jwt` |
| `actor_token` | Depends on `actorTokenContent` (may be absent) |
| `actor_token_type` | Depends on `actorTokenContent` (may be absent) |
| Client authentication | Always included (client_assertion or client_secret) |
| `scope` | Requested scopes for downstream resource |

**When to use:** When your IdP/downstream service implements the RFC 8693 standard (most modern IdPs, custom authorization servers).

---

### 2. JWT_AUTHORIZATION_GRANT (RFC 7523 §2.1)

The JWT Profile for OAuth 2.0 Authorization Grants.

**Wire format sent to token endpoint:**

| Parameter | Value |
|-----------|-------|
| `grant_type` | `urn:ietf:params:oauth:grant-type:jwt-bearer` |
| `assertion` | Inbound user JWT |
| `requested_token_use` | `on_behalf_of` (auto-added for Microsoft) |
| Client authentication | Always included |
| `scope` | Requested scopes |

**When to use:** When your IdP implements OBO through the JWT-bearer grant (primarily Microsoft Entra ID).

**Key difference:** No `actorTokenContent` configuration needed — the agent's identity comes purely from client authentication.

---

## actorTokenContent Options

These options only apply to `TOKEN_EXCHANGE` (RFC 8693) and control how the **agent/actor's identity** is communicated to the downstream token endpoint.

### `M2M` — Machine-to-Machine Token

**What AgentCore does:**
1. First performs a `client_credentials` grant against the same credential provider
2. Obtains an M2M access token for the agent
3. Sends that token as the `actor_token` in the exchange request

**Wire request includes:**

| Parameter | Value |
|-----------|-------|
| `subject_token` | User JWT |
| `actor_token` | M2M access token (agent identity) |
| `actor_token_type` | `urn:ietf:params:oauth:token-type:access_token` |
| Client auth | Always present |

**Use case:** Multi-tenant SaaS platforms where the downstream API needs to independently verify:
- Who the user is (subject_token)
- Which specific agent is acting (actor_token with its own scopes)
- Whether this agent is allowed to perform this type of delegation

**Example scenario:**
> A "financial-reports-agent" needs to access a downstream Analytics API on behalf of user Alice. The Analytics API checks: (1) Alice has access to financial data, (2) the agent is authorized for financial reporting operations (verified via actor_token scopes like `reports:read`).

**Configuration:**
```json
{
  "onBehalfOfTokenExchangeConfig": {
    "grantType": "TOKEN_EXCHANGE",
    "tokenExchangeGrantTypeConfig": {
      "actorTokenContent": "M2M",
      "actorTokenScopes": ["reports:read", "analytics:query"]
    }
  }
}
```

---

### `AWS_IAM_ID_TOKEN_JWT` — AWS IAM Identity Token

**What AgentCore does:**
1. Calls AWS STS to get a JWT identity token
2. Uses the credential provider's token endpoint as the `aud` claim
3. Sends that JWT as the `actor_token`

**Wire request includes:**

| Parameter | Value |
|-----------|-------|
| `subject_token` | User JWT |
| `actor_token` | AWS IAM identity token (JWT) |
| `actor_token_type` | `urn:ietf:params:oauth:token-type:jwt` |
| Client auth | Always present |

**Use case:** When the downstream service is AWS-aware and wants to verify that the acting workload is a specific AWS IAM principal.

**Example scenario:**
> An agent running on ECS as IAM role `arn:aws:iam::123456789:role/DataProcessingAgent` needs to call a partner's API on behalf of user Bob. The partner's authorization server trusts specific AWS roles — the actor_token proves the agent's AWS identity cryptographically.

**Configuration:**
```json
{
  "onBehalfOfTokenExchangeConfig": {
    "grantType": "TOKEN_EXCHANGE",
    "tokenExchangeGrantTypeConfig": {
      "actorTokenContent": "AWS_IAM_ID_TOKEN_JWT"
    }
  }
}
```

---

### `NONE` — No Actor Token

**What AgentCore does:**
- Does NOT send `actor_token` or `actor_token_type`
- The agent's identity is derived from **client authentication only**

**Wire request includes:**

| Parameter | Value |
|-----------|-------|
| `subject_token` | User JWT |
| `actor_token` | ❌ Not sent |
| `actor_token_type` | ❌ Not sent |
| Client auth | Always present (`client_assertion` + `client_assertion_type`) |

**Use case:** When the downstream authorization server identifies the agent solely from the client credentials used to authenticate the token exchange request. No separate actor token is needed.

**Example scenario:**
> An agent registered as OAuth client `invoice-agent-prod` calls Okta's token exchange endpoint on behalf of user Carol. Okta identifies the agent from the `client_assertion` (the registered client) and the user from the `subject_token`. No separate actor token needed.

**Configuration:**
```json
{
  "onBehalfOfTokenExchangeConfig": {
    "grantType": "TOKEN_EXCHANGE",
    "tokenExchangeGrantTypeConfig": {
      "actorTokenContent": "NONE"
    }
  }
}
```

**Important:** `NONE` only suppresses the actor token. Client authentication (`client_assertion`/`client_assertion_type` or `client_id`/`client_secret`) is ALWAYS sent regardless of this setting.

---

## Configuration Examples

### Example 1: Microsoft Entra ID OBO (Built-in Provider)

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --cli-input-json '{
    "name": "ms-entra-obo",
    "credentialProviderVendor": "MicrosoftOauth2",
    "oauth2ProviderConfigInput": {
      "microsoftOauth2ProviderConfig": {
        "oauthDiscovery": {
          "discoveryUrl": "https://login.microsoftonline.com/{tenant-id}/v2.0/.well-known/openid-configuration"
        },
        "clientId": "your-app-client-id",
        "clientSecret": "your-client-secret"
      }
    }
  }'
```

Uses `JWT_AUTHORIZATION_GRANT` automatically. Adds `requested_token_use=on_behalf_of` to the request.

---

### Example 2: Custom Provider with TOKEN_EXCHANGE + M2M Actor

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --cli-input-json '{
    "name": "custom-obo-with-actor",
    "credentialProviderVendor": "CustomOauth2",
    "oauth2ProviderConfigInput": {
      "customOauth2ProviderConfig": {
        "oauthDiscovery": {
          "discoveryUrl": "https://my.idp.com/.well-known/openid-configuration"
        },
        "clientId": "agent-client-id",
        "clientSecret": "agent-client-secret",
        "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
        "onBehalfOfTokenExchangeConfig": {
          "grantType": "TOKEN_EXCHANGE",
          "tokenExchangeGrantTypeConfig": {
            "actorTokenContent": "M2M",
            "actorTokenScopes": ["agent:delegate", "resource:access"]
          }
        }
      }
    }
  }'
```

---

### Example 3: Custom Provider with TOKEN_EXCHANGE + No Actor

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --cli-input-json '{
    "name": "custom-obo-no-actor",
    "credentialProviderVendor": "CustomOauth2",
    "oauth2ProviderConfigInput": {
      "customOauth2ProviderConfig": {
        "oauthDiscovery": {
          "discoveryUrl": "https://auth.okta.com/.well-known/openid-configuration"
        },
        "clientId": "agent-client-id",
        "clientSecret": "agent-client-secret",
        "clientAuthenticationMethod": "CLIENT_SECRET_POST",
        "onBehalfOfTokenExchangeConfig": {
          "grantType": "TOKEN_EXCHANGE",
          "tokenExchangeGrantTypeConfig": {
            "actorTokenContent": "NONE"
          }
        }
      }
    }
  }'
```

---

### Example 4: Custom Provider with JWT_AUTHORIZATION_GRANT

```bash
aws bedrock-agentcore-control create-oauth2-credential-provider \
  --cli-input-json '{
    "name": "custom-jwt-grant",
    "credentialProviderVendor": "CustomOauth2",
    "oauth2ProviderConfigInput": {
      "customOauth2ProviderConfig": {
        "oauthDiscovery": {
          "discoveryUrl": "https://my.idp.com/.well-known/openid-configuration"
        },
        "clientId": "agent-client-id",
        "clientSecret": "agent-client-secret",
        "clientAuthenticationMethod": "CLIENT_SECRET_BASIC",
        "onBehalfOfTokenExchangeConfig": {
          "grantType": "JWT_AUTHORIZATION_GRANT"
        }
      }
    }
  }'
```

---

## Runtime Usage

### Step 1: Get Workload Access Token

```bash
aws bedrock-agentcore get-workload-access-token-for-jwt \
    --workload-name my-agent \
    --user-token "<inbound-user-jwt>"

# Response:
{ "workloadAccessToken": "<workload-access-token>" }
```

### Step 2: Exchange for Downstream Token

```bash
aws bedrock-agentcore get-resource-oauth2-token \
    --resource-credential-provider-name my-obo-provider \
    --oauth2-flow ON_BEHALF_OF_TOKEN_EXCHANGE \
    --scopes "https://downstream.api/.default" \
    --workload-identity-token "<workload-access-token>"

# Response:
{ "accessToken": "<downstream-access-token>" }
```

### Step 3: Call Downstream Service

```bash
curl -H "Authorization: Bearer <downstream-access-token>" \
     https://downstream.api/resource
```

---

## Built-in Provider Support

| Provider | Grant Type | Notes |
|----------|-----------|-------|
| `MicrosoftOauth2` | `JWT_AUTHORIZATION_GRANT` | Auto-adds `requested_token_use=on_behalf_of`. Uses Microsoft's proprietary OBO flow built on RFC 7523. |
| `CustomOauth2` | `TOKEN_EXCHANGE` or `JWT_AUTHORIZATION_GRANT` | Full control over exchange configuration. Supports all `actorTokenContent` options. |

For providers not listed above, use `CustomOauth2` with the appropriate grant type.

---

## Known Limitations

### 1. Client Authentication Cannot Be Suppressed

**Issue:** AgentCore Identity always includes OAuth client authentication parameters in the token exchange request, regardless of `actorTokenContent` setting.

**Impact:** Downstream token endpoints that don't support client authentication (e.g., Databricks account federation) will reject the request.

**Affected scenario:** Databricks U2M integration where the token endpoint validates trust exclusively through JWT signature verification and does not accept `client_assertion`/`client_assertion_type` parameters.

**Workaround:** None currently available. Feature request: `clientAuthenticationMethod: "NONE"`.

**Status:** Reported — pending AC Identity team review.

---

### 2. Gateway Header Propagation on `tools/list` (Dynamic Listing)

**Issue:** When `listingMode: DYNAMIC` is enabled, interceptor-provided Authorization headers in `transformedGatewayRequest.headers` are not forwarded to MCP server targets during `tools/list` operations.

**Impact:** MCP servers that require user-scoped authentication to return tool lists (e.g., Databricks MCP with per-user access) cannot authenticate the listing request.

**Workaround:** None currently documented for `tools/list`. Header propagation via interceptors works for `tools/call` operations.

**Status:** Reported — pending AC Gateway team review.

---

## Decision Matrix: Which Configuration to Use?

| Downstream Service | Supports Client Auth? | Needs Agent Identity? | Recommended Config |
|---|---|---|---|
| Microsoft Entra ID | Yes (required) | From client auth | `MicrosoftOauth2` (built-in) |
| Okta / Auth0 | Yes | From client auth | `CustomOauth2` + `TOKEN_EXCHANGE` + `actorTokenContent: NONE` |
| Custom IdP (needs agent scopes) | Yes | Separate actor token | `CustomOauth2` + `TOKEN_EXCHANGE` + `actorTokenContent: M2M` |
| AWS-aware service | Yes | AWS IAM identity | `CustomOauth2` + `TOKEN_EXCHANGE` + `actorTokenContent: AWS_IAM_ID_TOKEN_JWT` |
| Databricks (JWT-trust only) | ❌ No | ❌ No | ⚠️ **Not supported today** — needs `clientAuthenticationMethod: NONE` |

---

## Runnable Examples

The shapes above are easier to internalize when you can run them. End-to-end working examples live under [`3-examples/`](./3-examples/):

| Use Case | What it demonstrates | Path |
|---|---|---|
| **UC1 — agent → downstream** | A single OBO hop **inside agent code**. Agent receives a user JWT, calls AgentCore Identity, gets a downstream token, calls the downstream API directly. | [`3-examples/01-agent-to-downstream/`](./3-examples/01-agent-to-downstream/) — Entra + Okta, both `local/` and `real-world/` |
| **UC2 — agent → Gateway → API** | **Two OBO hops in one chain**: OBO #1 in agent code, OBO #2 inside AgentCore Gateway. User identity preserved across all three tokens (`oid` on Entra, `sub`+`uid` on Okta). | [`3-examples/02-agent-via-gateway/`](./3-examples/02-agent-via-gateway/) — Entra + Okta, `real-world/` only |

UC2's `ARCHITECTURE.md` walks through how the same OBO primitive applies at two different layers (code vs infrastructure) and decodes the three tokens (T_user → T_gateway → T_downstream) so you can see the actor rotating (`azp`/`appid` on Entra, `cid` on Okta) while the user identity stays constant. On Entra the audience also rotates per hop; on Okta the audience stays constant at the auth server's audience and scope is what narrows. Read that for the cleanest illustration of why OBO chains are valuable.

---

## References

- [AgentCore OBO Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-behalf-of-token-exchange.html)
- [Gateway Interceptors Configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-configuration.html)
- [Blog: Fine-grained Access Control with Gateway Interceptors](https://aws.amazon.com/blogs/machine-learning/apply-fine-grained-access-control-with-bedrock-agentcore-gateway-interceptors/)
- [Blog: Securing AI Agents with AgentCore Identity](https://aws.amazon.com/blogs/security/securing-ai-agents-with-amazon-bedrock-agentcore-identity/)
- [RFC 8693 — OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693)
- [RFC 7523 — JWT Profile for OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc7523)
- [Microsoft OBO Flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
