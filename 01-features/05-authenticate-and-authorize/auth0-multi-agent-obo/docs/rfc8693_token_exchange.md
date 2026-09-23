# RFC 8693 Token Exchange Implementation

## Overview

This document describes the implementation of RFC 8693 (OAuth 2.0 Token Exchange) in the AgentCore Identity sample application, including the decision process, alternative approaches evaluated, and implementation details.

## Problem Statement

In the original architecture, the coordinator agent forwards the user's JWT unchanged to sub-agents. While simple, this violates the principle of least privilege — both the profile and accounts agents receive the full set of user scopes, even though each only needs a subset.

```
BEFORE: User JWT (all scopes) --> Coordinator --> [same JWT] --> Profile Agent
                                              --> [same JWT] --> Accounts Agent
```

RFC 8693 token exchange enables the coordinator to obtain attenuated tokens with only the scopes each sub-agent requires:

```
AFTER:  User JWT (all scopes) --> Coordinator --> [exchange] --> Profile Agent (profile scopes only)
                                              --> [exchange] --> Accounts Agent (account scopes only)
```

**Authorization source**: The coordinator determines the user's effective permissions by reading the Auth0 RBAC `permissions` claim (not the raw `scope` claim). The `permissions` claim reflects the user's role-based access, while `scope` reflects client-level grants that are identical for all users. The coordinator's tool-gating logic uses these RBAC permissions to decide which sub-agent tools to expose. Token exchange then attenuates the original JWT's `scope` claim per the scope policy for each target agent, providing a second layer of least-privilege enforcement. See [Scope vs Permissions Claims](auth0_configuration.md#scope-vs-permissions-claims) for details.

## Approaches Evaluated

### Approach A: Auth0 Native Token Exchange

**Description**: Use Auth0's `/oauth/token` endpoint with the RFC 8693 `urn:ietf:params:oauth:grant-type:token-exchange` grant type.

**Testing** (2026-02-11):
```
Auth0 Tenant: your-tenant.us.auth0.com
Grant Type:   urn:ietf:params:oauth:grant-type:token-exchange
Result:       400 - "Invalid subject_token_type"
```

All subject_token_type values tested:
- `urn:ietf:params:oauth:token-type:access_token` → Invalid subject_token_type
- `urn:ietf:params:oauth:token-type:jwt` → Invalid subject_token_type
- `urn:ietf:params:oauth:token-type:id_token` → Invalid subject_token_type
- `urn:ietf:params:oauth:token-type:refresh_token` → Invalid subject_token_type
- `access_token` / `jwt` → Invalid subject_token_type

Auth0 OIDC discovery returns empty `grant_types_supported`, confirming token exchange is not enabled on this tenant.

**Verdict**: Auth0 recognizes the grant type (does not return `unsupported_grant_type`) but the token exchange feature requires Auth0 Organizations or Enterprise plan configuration that isn't present. **Not viable without Auth0 tenant modifications.**

**Pros**:
- Auth0 manages signing, JWKS, and token lifecycle
- Tokens are issued by the trusted IdP

**Cons**:
- Requires specific Auth0 plan tier
- Exchange logic is opaque (inside Auth0)
- Network latency for each exchange call
- Cannot demonstrate RFC 8693 mechanics in code

### Approach B: Self-Contained Token Exchange Service (Implemented)

**Description**: Build a `TokenExchangeService` within the coordinator that implements RFC 8693 semantics locally. The service generates an RSA key pair, receives the user's JWT, and mints a new token with attenuated scopes signed with its own key.

**Verdict**: **Selected** — maximizes educational value, no external dependencies, full RFC 8693 compliance visible in code.

**Pros**:
- All exchange logic visible in codebase
- No Auth0 plan dependencies
- No network latency per exchange
- Full RFC 8693 request/response format
- `act` claim delegation chain fully controlled
- Maximum explainability for enablement

**Cons**:
- Coordinator must manage RSA key pair
- Sub-agents must validate tokens from two issuers (Auth0 + exchange service)
- Key rotation must be handled (mitigated by short token lifetime)

### Approach C: Mock/Simulated Exchange

**Description**: Keep JWT forwarding but modify the payload to strip scopes, without re-signing the token.

**Verdict**: **Rejected** — modifying JWT payload without re-signing breaks the token signature. Sub-agents cannot validate tampered tokens. Does not comply with RFC 8693.

**Pros**:
- Simplest implementation
- No key management

**Cons**:
- Breaks JWT signature integrity
- Not true RFC 8693 compliance
- Sub-agents cannot validate modified tokens
- Misleading for educational purposes

## Implementation Details

### Token Exchange Service (`shared/auth/token_exchange.py`)

The service implements RFC 8693 Section 2.1 (Request) and Section 2.2 (Response).

**Exchange Flow**:
1. Coordinator receives user's JWT from inbound request
2. Before calling a sub-agent, coordinator creates a `TokenExchangeRequest`
3. Service validates the request format
4. Service decodes the subject token (already validated by AgentCore on inbound)
5. Service resolves the scope policy for the target agent
6. Service computes attenuated scopes (intersection of original and allowed)
7. Service builds new JWT with `act` claim, attenuated scopes, and short expiry
8. Service signs with RSA private key and returns `TokenExchangeResponse`

### Scope Attenuation Policy (Fine-Grained Model)

The scope model uses resource-level scopes for maximum visibility in the demo. Each agent receives only the scopes relevant to its domain, with 6 scopes stripped per exchange.

#### Original JWT (13 scopes from Auth0)

```
openid profile email
profile:personal:read profile:personal:write
profile:preferences:read profile:preferences:write
accounts:savings:read accounts:savings:write
accounts:transaction:read
accounts:credit:read accounts:credit:write
accounts:investment:read
```

#### Attenuated to Profile Agent (7 scopes — 6 removed)

```
openid profile email
profile:personal:read profile:personal:write
profile:preferences:read profile:preferences:write
```

**Removed:** All `accounts:*` scopes (savings, transaction, credit, investment)

#### Attenuated to Accounts Agent (7 scopes — 6 removed)

```
openid
accounts:savings:read accounts:savings:write
accounts:transaction:read
accounts:credit:read accounts:credit:write
accounts:investment:read
```

**Removed:** `profile`, `email`, all `profile:*` scopes

#### Scope Reference Table

| Scope | Description | Profile Agent | Accounts Agent |
|---|---|:---:|:---:|
| `openid` | OIDC standard | Y | Y |
| `profile` | OIDC user info | Y | - |
| `email` | OIDC email | Y | - |
| `profile:personal:read` | Read name, DOB, address, phone | Y | - |
| `profile:personal:write` | Update address, phone, email | Y | - |
| `profile:preferences:read` | Read contact/marketing prefs | Y | - |
| `profile:preferences:write` | Update contact/marketing prefs | Y | - |
| `accounts:savings:read` | View savings balances/details | - | Y |
| `accounts:savings:write` | Savings operations | - | Y |
| `accounts:transaction:read` | View transaction account | - | Y |
| `accounts:credit:read` | View credit card balances | - | Y |
| `accounts:credit:write` | Credit card settings | - | Y |
| `accounts:investment:read` | View investment portfolio | - | Y |

#### Scope-Based Account Filtering

The accounts agent uses fine-grained scopes for functional filtering (not just cosmetic attenuation):

| Token Scope | Account Types Visible |
|---|---|
| `accounts:savings:read` | Savings accounts only |
| `accounts:transaction:read` | Transaction accounts only |
| `accounts:credit:read` | Credit card accounts only |
| `accounts:investment:read` | Investment accounts only |

#### Deprecated Legacy Scopes

Legacy scopes (`customer:read`, `customer:write`, `accounts:read`) are deprecated and no longer accepted by the scope policies or auth validators. All deployments must use the fine-grained scope model.

The policy is enforced via `ScopePolicy.attenuate()` which computes the set intersection of original scopes and allowed scopes. This guarantees:
- **Non-elevation**: Exchanged token can never have scopes not in the original
- **Least privilege**: Each agent gets only what it needs
- **Deterministic**: Same input always produces same scope set

### Exchanged Token Claims

```json
{
  "sub": "auth0|123456",
  "email": "user@example.com",
  "iss": "urn:agentcore:token-exchange-service",
  "aud": "customer_profile_agent",
  "exp": 1739318400,
  "iat": 1739318100,
  "jti": "tex-a1b2c3d4e5f6",
  "scope": "email openid profile profile:personal:read profile:personal:write profile:preferences:read profile:preferences:write",
  "act": {
    "sub": "coordinator-agent"
  },
  "original_issuer": "https://your-tenant.us.auth0.com/",
  "original_audience": "https://agentcore-financial-api",
  "exchange_id": "tex-a1b2c3d4e5f6",
  "https://agentcore.example.com/customer_id": "CUST-001"
}
```

### Key RFC 8693 Compliance Points

| RFC Section | Requirement | Implementation |
|---|---|---|
| 2.1 | grant_type = `urn:ietf:params:oauth:grant-type:token-exchange` | `TokenExchangeRequest.grant_type` |
| 2.1 | subject_token required | Validated in `TokenExchangeRequest.validate()` |
| 2.1 | subject_token_type required | Supports `jwt` and `access_token` types |
| 2.1 | audience identifies target | Set to target agent identifier |
| 2.1 | scope must not exceed original | `ScopePolicy.attenuate()` enforces intersection |
| 2.2 | access_token in response | `TokenExchangeResponse.access_token` |
| 2.2 | issued_token_type in response | Set to `urn:ietf:params:oauth:token-type:jwt` |
| 2.2 | expires_in in response | Default 300 seconds (5 minutes) |
| 4.4 | act claim for delegation | `{"act": {"sub": "coordinator-agent"}}` |

### Sub-Agent Dual-Issuer Validation

Sub-agents now accept tokens from two issuers:
1. **Auth0** (original): Direct invocations with user JWT
2. **Token Exchange Service** (`urn:agentcore:token-exchange-service`): Exchanged tokens from coordinator

When an exchanged token is detected (presence of `act` claim), the validator:
- Logs the delegation chain for audit
- Validates scope is appropriate for this agent
- Preserves existing authorization logic

### Security Considerations

- **Short-lived tokens**: 5-minute expiry minimizes exposure window
- **RSA signing**: 2048-bit key ensures exchanged tokens cannot be forged
- **Non-elevation guarantee**: Set intersection prevents scope escalation
- **Audit trail**: Every exchange logged with unique `exchange_id`
- **Delegation chain**: `act` claim provides tamper-proof delegation evidence
- **Key isolation**: Private key exists only in coordinator runtime memory

## Auth0 Configuration for Fine-Grained Scopes

To enable the fine-grained scope model in Auth0:

### 1. Update API Scopes

**Auth0 Dashboard > Applications > APIs > AgentCore Financial API > Permissions tab**

Old custom scopes (`customer:read`, `customer:write`, `accounts:read`) have been removed.

Add new scopes:

| Scope | Description |
|---|---|
| `profile:personal:read` | Read personal details |
| `profile:personal:write` | Update personal details |
| `profile:preferences:read` | Read preferences |
| `profile:preferences:write` | Update preferences |
| `accounts:savings:read` | Read savings accounts |
| `accounts:savings:write` | Savings operations |
| `accounts:transaction:read` | Read transaction accounts |
| `accounts:credit:read` | Read credit cards |
| `accounts:credit:write` | Credit card operations |
| `accounts:investment:read` | Read investments |

### 2. No Client Code Changes Needed

The Streamlit app reads scopes from `Auth0Config.scopes` in `shared/config/settings.py`, which has been updated to request all fine-grained scopes.

### 3. No Auth0 Action Changes Needed

The PostLogin Action injects custom claims (customer_id, roles, etc.) which are independent of the scope model. Scopes are granted by the API definition, not the Action.

## Pivot Guide

If the Auth0 tenant is upgraded to support native token exchange:

1. Replace `TokenExchangeService` initialization with Auth0 HTTP client
2. Call `POST https://{domain}/oauth/token` with RFC 8693 parameters
3. Remove RSA key management from coordinator
4. Update sub-agents to validate only Auth0-issued tokens
5. Auth0 JWKS endpoint handles key rotation automatically

The `TokenExchangeRequest` and `TokenExchangeResponse` dataclasses remain compatible as they follow the RFC 8693 wire format.
