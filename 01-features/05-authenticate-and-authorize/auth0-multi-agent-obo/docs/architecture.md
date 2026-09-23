# Architecture Documentation

Comprehensive architecture documentation for the AgentCore Identity sample project.

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          User Layer                              │
│  ┌──────────────┐          ┌──────────────┐                     │
│  │   Browser    │          │   Mobile     │                     │
│  │  (Streamlit) │          │     App      │                     │
│  └──────┬───────┘          └──────┬───────┘                     │
└─────────┼──────────────────────────┼──────────────────────────────┘
          │                          │
          │ HTTPS/OAuth              │
          ▼                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Identity Layer                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     Auth0 (Okta)                         │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │   │
│  │  │ Applications │  │     APIs     │  │    Users     │   │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘   │   │
│  │  ┌──────────────┐  ┌──────────────┐                    │   │
│  │  │   Actions    │  │    Rules     │                    │   │
│  │  └──────────────┘  └──────────────┘                    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────┬───────────────────────────────────────┘
                          │ JWT Token
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AgentCore Runtime                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                  Identity Pool                           │   │
│  │    ┌─────────────────────────────────────────┐           │   │
│  │    │       JWT Authorizer                    │           │   │
│  │    │  - Validates Token Signature           │           │   │
│  │    │  - Checks Expiration                   │           │   │
│  │    │  - Verifies Audience & Issuer          │           │   │
│  │    │  - Extracts Custom Claims              │           │   │
│  │    └─────────────────────────────────────────┘           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Coordinator Agent                           │   │
│  │  - Intent Detection                                      │   │
│  │  - Request Routing                                       │   │
│  │  - Response Aggregation                                  │   │
│  │  - Context Management                                    │   │
│  └────────┬─────────────────────────────────────────────────┘   │
│           │                                                      │
│           │ Routes to Action Agents                             │
│           │                                                      │
│  ┌────────┴──────────────────────────────────────────────────┐  │
│  │              Action Agents Layer                          │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │  │
│  │  │   Profile   │  │  Accounts   │  │Transactions │      │  │
│  │  │    Agent    │  │    Agent    │  │    Agent    │      │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘      │  │
│  │  ┌─────────────┐                                         │  │
│  │  │    Cards    │                                         │  │
│  │  │    Agent    │                                         │  │
│  │  └─────────────┘                                         │  │
│  └────────┬──────────────────────────────────────────────────┘  │
│           │                                                      │
│  ┌────────┴──────────────────────────────────────────────────┐  │
│  │              Memory Service                               │  │
│  │  - Session Management                                     │  │
│  │  - Conversation History                                   │  │
│  │  - Context Persistence                                    │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data Layer                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  DynamoDB   │  │     S3      │  │   Secrets   │             │
│  │  (Profiles) │  │   (Files)   │  │   Manager   │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

## Authentication Flow Detail

### OAuth 2.0 Authorization Code Flow with PKCE

```
User                Browser              Streamlit App         Auth0           AgentCore
  │                    │                       │                 │                 │
  │  1. Click Login    │                       │                 │                 │
  │───────────────────->│                       │                 │                 │
  │                    │                       │                 │                 │
  │                    │  2. Generate State &  │                 │                 │
  │                    │     Code Verifier     │                 │                 │
  │                    │──────────────────────->│                 │                 │
  │                    │                       │                 │                 │
  │                    │  3. Redirect to       │                 │                 │
  │                    │     Auth0 /authorize  │                 │                 │
  │                    │<-──────────────────────│                 │                 │
  │                    │                       │                 │                 │
  │  4. Display Login  │                       │                 │                 │
  │<-───────────────────│                       │  GET /authorize │                 │
  │                    │───────────────────────────────────────->│                 │
  │                    │                       │                 │                 │
  │  5. Enter Creds    │                       │                 │                 │
  │───────────────────->│                       │                 │                 │
  │                    │  POST /usernamepassword/login           │                 │
  │                    │───────────────────────────────────────->│                 │
  │                    │                       │                 │                 │
  │                    │                       │  6. Run Actions │                 │
  │                    │                       │     (Add Claims)│                 │
  │                    │                       │  ┌──────────────┤                 │
  │                    │                       │  │              │                 │
  │                    │                       │  └─->            │                 │
  │                    │                       │                 │                 │
  │  7. Redirect       │                       │                 │                 │
  │    with Auth Code  │                       │                 │                 │
  │<-───────────────────┼───────────────────────────────────────│                 │
  │                    │                       │                 │                 │
  │                    │  8. Exchange Code     │                 │                 │
  │                    │     for Tokens        │                 │                 │
  │                    │──────────────────────->│  POST /oauth/token              │
  │                    │                       │───────────────->│                 │
  │                    │                       │                 │                 │
  │                    │  9. Return Tokens     │                 │                 │
  │                    │     (JWT)             │                 │                 │
  │                    │<-──────────────────────│<-───────────────│                 │
  │                    │                       │                 │                 │
  │                    │  10. Store Tokens     │                 │                 │
  │                    │      in Session       │                 │                 │
  │                    │──────────────────────->│                 │                 │
  │                    │                       │                 │                 │
  │  11. Show Dashboard│                       │                 │                 │
  │<-───────────────────│                       │                 │                 │
  │                    │                       │                 │                 │
  │  12. Make Request  │                       │                 │                 │
  │───────────────────->│  13. Add JWT to       │                 │                 │
  │                    │      Request          │                 │                 │
  │                    │──────────────────────->│  14. Invoke Agent                │
  │                    │                       │     with JWT    │                 │
  │                    │                       │─────────────────────────────────->│
  │                    │                       │                 │  15. Validate   │
  │                    │                       │                 │      JWT        │
  │                    │                       │                 │  ┌──────────────┤
  │                    │                       │                 │  │              │
  │                    │                       │                 │  └─->            │
  │                    │                       │                 │  16. Extract    │
  │                    │                       │                 │      Claims     │
  │                    │                       │                 │  ┌──────────────┤
  │                    │                       │                 │  │              │
  │                    │                       │                 │  └─->            │
  │                    │                       │  17. Agent      │                 │
  │                    │                       │      Response   │                 │
  │                    │  18. Display Response │<-────────────────────────────────│
  │<-───────────────────│<-──────────────────────│                 │                 │
```

## Agent-to-Agent Communication

### RFC 8693 Token Exchange with Scope Attenuation

The coordinator uses RFC 8693 token exchange to issue attenuated tokens before invoking sub-agents.
Each sub-agent receives only the scopes relevant to its domain, enforcing least-privilege access.

**Deployment Requirement**: Agents must be deployed with `--request-header-allowlist "Authorization"`
to enable direct JWT access via `context.request_headers.get('Authorization')`.

```
                                    ┌─────────────────┐
                                    │   Okta / Auth0  │
                                    │   (IdP/OIDC)    │
                                    └────────┬────────┘
                                             │
                         1. OAuth 2.0 Flow   │  2. JWT Issued (13 scopes)
                            (PKCE)           │     (id_token + access_token)
                                             │
                                             ▼
┌─────────────┐                      ┌───────────────┐
│    User     │ ───────────────────-> │    Client     │
│  (Browser)  │  Login via IdP       │    (SPA)      │
└─────────────┘                      └───────┬───────┘
                                             │
                                             │ 3. HTTP POST
                                             │    Authorization: Bearer {jwt}
                                             ▼
                                     ┌───────────────────┐
                                     │  AgentCore        │
                                     │  Runtime          │
                                     │  ┌─────────────┐  │
                                     │  │ JWT         │  │  4. Validate JWT
                                     │  │ Authorizer  │◄─┼──── against IdP JWKS
                                     │  └──────┬──────┘  │
                                     │         │         │
                                     │         ▼         │
                                     │  ┌─────────────┐  │
                                     │  │ Coordinator │  │  5. Extract claims, scope-gate
                                     │  │   Agent     │  │     tools, exchange token
                                     │  └──────┬──────┘  │
                                     └─────────┼─────────┘
                                               │
                                               │ 6. RFC 8693 Token Exchange
                                               │    Attenuated JWT (7 scopes per agent)
                                               ▼
                                     ┌───────────────────┐
                                     │  AgentCore        │
                                     │  Runtime          │
                                     │  ┌─────────────┐  │
                                     │  │ Dual-Issuer │  │  7. Validate exchanged token
                                     │  │ Validator   │◄─┼──── (Auth0 OR exchange service)
                                     │  └──────┬──────┘  │
                                     │         │         │
                                     │         ▼         │
                                     │  ┌─────────────┐  │
                                     │  │   Action    │  │  8. Verify scopes match agent
                                     │  │   Agent     │  │     domain, authorize & execute
                                     │  └─────────────┘  │
                                     └───────────────────┘
```

### Flow Steps

| Step | Description |
|------|-------------|
| 1 | User initiates login, redirected to IdP (Okta/Auth0) |
| 2 | User authenticates, IdP issues JWT with 13 fine-grained scopes and custom claims |
| 3 | Client sends request to Coordinator with `Authorization: Bearer {jwt}` |
| 4 | AgentCore JWT Authorizer validates signature against IdP's JWKS endpoint |
| 5 | Coordinator extracts claims, scope-gates tools (users without accounts scopes don't see accounts tool), checks permissions before routing |
| 6 | Coordinator performs RFC 8693 token exchange: strips scopes not relevant to the target agent (6 scopes removed per exchange) |
| 7 | Sub-agent validates the exchanged token (dual-issuer: Auth0 or token exchange service) |
| 8 | Sub-agent verifies scopes are appropriate for its domain, authorizes request, executes operation |

### Key Principles

- **RFC 8693 token exchange** for scope attenuation at each agent boundary
- **Scope-gated tools**: users without `accounts:*` scopes don't see the accounts tool in the LLM
- **Permission check before exchange**: coordinator returns PERMISSION_DENIED without attempting token exchange if scopes are missing
- **Dual-issuer validation**: sub-agents accept tokens from Auth0 (direct) or the token exchange service (via coordinator)
- **Non-elevation guarantee**: exchanged tokens can never have more scopes than the original (set intersection)
- **`act` claim delegation chain**: tracks which agent performed the exchange for audit
- **Fine-grained scopes**: 13 resource-level scopes replace 3 legacy coarse scopes

### Implementation Files

| File | Purpose |
|------|---------|
| `shared/auth/token_exchange.py` | RFC 8693 token exchange service with scope policies |
| `agents/coordinator/agent.py` | Token exchange orchestration, scope-gated tool selection |
| `agents/coordinator/subagent_router.py` | Routes to action agents with exchanged tokens |
| `agents/coordinator/main.py` | JWT extraction from `context.request_headers` |
| `agents/accounts/auth_validator.py` | Dual-issuer validation for accounts agent |
| `agents/customer_profile/auth_validator.py` | Dual-issuer validation for profile agent |
| `scripts/deploy_all_agents.sh` | Deployment with `--request-header-allowlist` |

### Request Flow Through Coordinator (Detailed)

```
Client Request (JWT with 13 scopes)
      │
      ▼
┌─────────────────┐
│  Coordinator    │  1. Receives request with JWT
│     Agent       │  2. Scope-gates tools (removes accounts tool if no accounts scopes)
└────────┬────────┘  3. Detects intent, determines target agent
         │           4. Checks scopes before routing (safety net)
         │           5. Exchanges token via RFC 8693 (scope attenuation)
         │
         ├───────────────────────────────┐
         │                               │
         ▼                               ▼
    ┌─────────┐                    ┌─────────┐
    │ Profile │                    │Accounts │
    │  Agent  │                    │  Agent  │
    └────┬────┘                    └────┬────┘
         │                              │
         │ 1. Receive exchanged         │ 1. Receive exchanged
         │    token (7 scopes)          │    token (7 scopes)
         │                              │
         │ 2. Dual-issuer               │ 2. Dual-issuer
         │    validation                │    validation
         │                              │
         │ 3. Verify profile:*          │ 3. Verify accounts:*
         │    scopes present            │    scopes present
         │                              │
         │ 4. Extract claims,           │ 4. Extract claims,
         │    authorize request         │    filter by account-type scopes
         │                              │
         │ 5. Execute operation         │ 5. Execute operation
         │                              │
         ▼                              ▼
    ┌──────────────────────────────────────────┐
    │                 DynamoDB                   │
    └──────────────────────────────────────────┘
```

## Token Validation Architecture

### JWT Validation Process

```
┌──────────────────────────────────────────────────────────────┐
│                    JWT Token Arrives                         │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │  1. Extract from Authorization │
        │     Header: Bearer <token>     │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  2. Parse JWT Structure        │
        │     - Header                   │
        │     - Payload                  │
        │     - Signature                │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  3. Fetch JWKS from Auth0      │
        │     https://tenant.auth0.com/  │
        │     .well-known/jwks.json      │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  4. Verify Signature           │
        │     - Get public key from JWKS │
        │     - Verify RS256 signature   │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  5. Validate Claims            │
        │     - iss: correct issuer?     │
        │     - aud: correct audience?   │
        │     - exp: not expired?        │
        │     - iat: issued in past?     │
        │     - nbf: not before check    │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  6. Extract Custom Claims      │
        │     namespace/customer_id      │
        │     namespace/account_types    │
        │     namespace/roles            │
        │     namespace/kyc_status       │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  7. Build User Context         │
        │     {                          │
        │       userId: "auth0|123",     │
        │       customerId: "CUST-123",  │
        │       accountTypes: [...],     │
        │       roles: [...],            │
        │       scopes: [...]            │
        │     }                          │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  8. Authorize Operation        │
        │     - Check scopes             │
        │     - Verify resource access   │
        │     - Validate role            │
        └───────────────┬────────────────┘
                        │
                        ▼
        ┌────────────────────────────────┐
        │  9. Execute Agent Operation    │
        └────────────────────────────────┘
```

## Authorization Model

### Multi-Layer Authorization

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Network Layer (AWS)                                │
│   - VPC Security Groups                                     │
│   - Network ACLs                                            │
│   - API Gateway                                             │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] Network allowed
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Authentication (AgentCore Identity Pool)           │
│   - JWT signature verification                              │
│   - Token expiration check                                  │
│   - Issuer & audience validation                            │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] Authenticated
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Scope-Based Authorization                          │
│   - profile:personal:read  -> Can read profile data          │
│   - profile:personal:write -> Can modify profile data        │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] Has required scopes
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Resource-Level Authorization                       │
│   - Customer can only access own profile                    │
│   - Account IDs in token must match requested accounts      │
│   - Verify customer_id matches resource owner               │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] Owns resource
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 5: Role-Based Authorization                           │
│   - Customer role -> Basic operations                       │
│   - Premium role -> Enhanced features                       │
│   - Admin role -> Full access                               │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] Has required role
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 6: KYC-Based Authorization                            │
│   - verified -> Full transaction capabilities               │
│   - pending -> Limited functionality                        │
│   - failed -> Restricted operations                         │
└────────────────┬────────────────────────────────────────────┘
                 │ [PASS] KYC status allows operation
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ Operation Executed                                           │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Profile Read Operation

```
1. User Request
   |
2. Streamlit App
   - Add JWT to Authorization header
   |
3. AgentCore Gateway
   - Route to Identity Pool
   |
4. Identity Pool
   - Validate JWT with Auth0 JWKS
   - Extract claims
   |
5. Coordinator Agent
   - Detect intent: "get profile"
   - Exchange token (RFC 8693): strip accounts:* scopes
   - Route to Profile Agent with attenuated token
   |
6. Profile Agent
   - Validate exchanged token (dual-issuer)
   - Extract customer_id from claims
   - Check scopes: profile:personal:read
   |
7. DynamoDB
   - Query: customer_id = "CUST-12345"
   - Return profile data
   |
8. Profile Agent
   - Format response
   - Return to Coordinator
   |
9. Coordinator Agent
   - Aggregate response
   - Return to client
   |
10. Streamlit App
    - Display profile to user
```

### Profile Update Operation

```
1. User Request: "Update email to new@example.com"
   |
2. Streamlit App
   - Add JWT to Authorization header
   - Include new email in payload
   |
3. AgentCore Gateway
   - Validate JWT
   |
4. Coordinator Agent
   - Detect intent: "update profile"
   - Extract: field=email, value=new@example.com
   - Exchange token (RFC 8693): strip accounts:* scopes
   - Route to Profile Agent with attenuated token
   |
5. Profile Agent
   - Validate exchanged token (dual-issuer)
   - Extract customer_id from claims
   - Check scopes: profile:personal:write [PASS]
   - Validate email format
   - Check customer_id matches token
   |
6. DynamoDB
   - Update item
   - customer_id = "CUST-12345"
   - SET email = "new@example.com"
   - SET updated_at = timestamp
   |
7. Profile Agent
   - Confirm update
   - Return updated profile
   |
8. Coordinator Agent
   - Format success response
   |
9. Streamlit App
   - Display: "Email updated successfully"
   - Refresh profile display
```

## Security Architecture

### Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Transport Security                                           │
│   - TLS 1.3                                                  │
│   - Certificate Pinning                                      │
│   - HTTPS Only                                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Authentication                                               │
│   - OAuth 2.0 with PKCE                                      │
│   - JWT with RS256 signatures                                │
│   - Short-lived tokens (24h)                                 │
│   - Refresh token rotation                                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Authorization                                                │
│   - Scope-based access control                               │
│   - Resource-level permissions                               │
│   - Role-based authorization                                 │
│   - KYC-based restrictions                                   │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Data Protection                                              │
│   - Encryption at rest (DynamoDB)                            │
│   - Encryption in transit (TLS)                              │
│   - Secrets Manager for credentials                          │
│   - PII data masking in logs                                 │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Audit & Monitoring                                           │
│   - CloudWatch Logs                                          │
│   - CloudTrail for API calls                                 │
│   - Auth0 logs for authentication events                     │
│   - X-Ray tracing                                            │
└─────────────────────────────────────────────────────────────┘
```

## Scalability Considerations

### Horizontal Scaling

- **AgentCore**: Automatically scales based on request volume
- **DynamoDB**: On-demand capacity or provisioned with auto-scaling
- **Auth0**: Cloud-based, automatically scales
- **Streamlit**: Deploy multiple instances behind load balancer

### Performance Optimization

- **JWKS Caching**: Cache Auth0 public keys (24h TTL)
- **Session Caching**: Use Redis for session state (multi-instance)
- **Database Indexes**: GSI on user_id, kyc_status
- **Connection Pooling**: Reuse HTTP connections to AgentCore

## Deployment Architecture

### Current MVP Deployment (2026-02-11)

```
Region: us-east-1 (or your preferred AWS region)
├── AgentCore Runtime
│   ├── Coordinator Agent   (<COORDINATOR_AGENT_ID>)              READY
│   ├── Profile Agent       (<PROFILE_AGENT_ID>)                  READY
│   └── Accounts Agent      (<ACCOUNTS_AGENT_ID>)                 READY
├── ECR Repositories (3)
│   ├── coordinator-agent
│   ├── customer-profile-agent
│   └── accounts-agent
├── CloudWatch
│   ├── Log groups (per-agent)
│   ├── X-Ray sampling (100%)
│   └── GenAI Observability (OTEL spans)
└── Secrets Manager (agentcore/auth0)

Auth0 (Global)
└── Tenant: your-tenant.us.auth0.com
```

### Observability

All agents are instrumented with AWS OpenTelemetry (ADOT) auto-instrumentation:

- **Coordinator**: Uses `aws_opentelemetry_distro_genai_beta` for GenAI-specific spans (`gen_ai.tool.message`, `gen_ai.choice`, `gen_ai.system.message`)
- **Sub-agents**: Use standard `aws-opentelemetry-distro` for HTTP tracing
- **Trace propagation**: W3C `traceparent` header injected by coordinator's HTTP client, enabling correlated multi-agent traces in CloudWatch GenAI Observability

Traces are visible at: `CloudWatch → Application Signals (APM) → GenAI Observability → AgentCore`

## Next Steps

- Review [Setup Guide](setup.md) for deployment
- See [API Reference](api_reference.md) for endpoints
- Check [Auth0 Configuration](auth0_configuration.md) for identity setup
