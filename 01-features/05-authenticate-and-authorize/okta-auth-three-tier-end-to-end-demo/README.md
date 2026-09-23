# Okta OAuth2 Three-Tier Authentication with Amazon Bedrock AgentCore

Per-tier token isolation with pure Okta OAuth2 authentication and RBAC-based access control.

## Architecture

![Okta Three-Tier Architecture](images/okta_architecture.png)

```
User (Okta JWT: agent:invoke)
  | Validates user token via Okta OIDC
Agent Runtime (Okta JWT: gateway:invoke)
  | Validates agent token via Okta OIDC + allowedAudience
Gateway
  |-- OAuth2 Credential Provider (tool sync -- control plane)
  +-- Interceptor Lambda (token exchange -- data plane)
  | Fresh Okta JWT (scope: mcp:invoke)
MCP Server Runtime
  | Validates MCP token via Okta OIDC
  | RBAC check on caller's group -> returns or denies data
```

### Control Plane (Tool Sync)

```
Gateway -> OAuth2 Credential Provider -> MCP Server
```

Happens once during Gateway target creation. Uses OAuth2 `client_credentials` grant to discover available tools.

### Data Plane (Runtime Requests)

```
User -> Agent Runtime -> Gateway -> Interceptor Lambda -> MCP Server
```

Happens on every tool call. The Interceptor Lambda exchanges the Agent token for a fresh MCP token. No token is ever forwarded downstream.

## Token Isolation

| Token | Scope | Issued To | Used For |
|-------|-------|-----------|----------|
| User Token | `agent:invoke` | End user | Call Agent Runtime |
| Agent Token | `gateway:invoke` | Agent Runtime | Call Gateway |
| MCP Token | `mcp:invoke` | Gateway Interceptor | Call MCP Server |

Each tier validates its inbound JWT via Okta OIDC (signature, expiry, audience, issuer). No token is ever forwarded downstream.

## RBAC (Role-Based Access Control)

The MCP Server enforces group-based access control using the caller's Okta group membership propagated via security headers:

| Group | list_projects | property_details | budget_summary |
|-------|:---:|:---:|:---:|
| `engineering-admin` | Yes | Yes | Yes |
| `finance-viewer` | Yes | No | Yes |
| (no group) | No | No | No |

Demo users:
- Alice (`alice@example.com`) in `engineering-admin` -- full access to all tools
- Bob (`bob@example.com`) in `finance-viewer` -- can list projects and view budgets, but cannot access property details

## Custom Security Headers (End-to-End)

Three custom headers propagate the end-user's identity from the caller all the way to the MCP Server:

| Header | Purpose |
|--------|---------|
| `X-Amzn-Bedrock-AgentCore-Runtime-Custom-End-User-Id` | Caller's user ID |
| `X-Amzn-Bedrock-AgentCore-Runtime-Custom-End-User-Email` | Caller's email |
| `X-Amzn-Bedrock-AgentCore-Runtime-Custom-End-User-Groups` | Caller's Okta groups |

Propagation flow:
```
User (HTTP headers) -> Agent Runtime -> Gateway -> Interceptor Lambda -> MCP Server
```

The Interceptor Lambda extracts these headers from the inbound Gateway request and injects them into the JSON-RPC body as `params._meta.security_context`. The MCP Server's ASGI middleware reads from HTTP headers first, then falls back to the `_meta` body field.

Each tool response includes a `_security_context` field so callers can verify the headers arrived and see which RBAC rules were applied.

## Prerequisites

- Python 3.10+
- AWS credentials configured with AgentCore permissions
- Okta developer account with:
  - 1 custom Authorization Server
  - 3 OAuth2 apps: Agent (API Service), Gateway (API Service), User (Web Application)
  - 3 scopes (`agent:invoke`, `gateway:invoke`, `mcp:invoke`) plus `openid`, `email`, `groups` for user app
  - 2 Okta users with group assignments for RBAC testing
  - DPoP disabled on all apps

## Okta Setup

See the notebook's Okta Setup Reference section for step-by-step screenshots.

For a detailed walkthrough, see the [Step-by-Step Okta Integration for Gateway Auth](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/main/06-workshops/03-AgentCore-identity/08-IDP-examples/Okta/Step_by_Step_Okta_Integration_for_Gateway_Auth.ipynb) notebook.

### 1. Applications

Create three apps in Applications > Applications:
- Agent App and Gateway App: API Service Integration (Client Credentials)
- User Web App: Web Application (Authorization Code grant)
  - Sign-in redirect URI: `http://localhost:8080/callback`

![Okta Applications](images/okta_01_applications.png)

### 2. Authorization Server

Navigate to Security > API and create a custom authorization server:

![Authorization Server](images/okta_02_auth_server_nav.png)

### 3. Scopes

Define scopes on the authorization server:

![Scopes](images/okta_03_auth_server_scopes.png)

### 4. Claims and Access Policies

Add `client_id`, `email`, and `groups` claims. Create an access policy with rules per app:

![Claims and Access Policies](images/okta_04_claims_access_policies.png)

### 5. Access Policy Rules

| Rule | App | Grant Type | Scope |
|------|-----|------------|-------|
| AgentCore Agent App | Agent Runtime | Client Credentials | `gateway:invoke` |
| AgentCore Gateway App | Interceptor Lambda | Client Credentials | `mcp:invoke` |
| AgentCore User Web App | End User | Authorization Code | `agent:invoke openid email groups` |

![Rule: Agent App](images/okta_05_rule_agent_app.png)
![Rule: Gateway App](images/okta_06_rule_gateway_app.png)
![Rule: User Web App](images/okta_07_rule_user_app.png)

### 6. Users and Groups

Create two Okta users and assign them to groups:
- `alice@example.com` -> group `engineering-admin` (full access)
- `bob@example.com` -> group `finance-viewer` (limited access)

![Users](images/okta_08_users.png)
![Group: engineering-admin](images/okta_09_group_engineering_admin.png)
![Group: finance-viewer](images/okta_10_group_finance_viewer.png)

### 7. User Web App (Authorization Code)

Create a Web Application for end-user authentication:

![Create App Integration](images/okta_11_create_app_integration.png)
![User Web App Config](images/okta_12_user_web_app_config.png)
![User Web App Assignments](images/okta_13_user_web_app_assignments.png)

### 8. Access Policies

Configure access policies with rules for each app:

![Access Policies](images/okta_14_access_policies.png)

### 9. Authentication Policies (Test Users)

For demo/test users, configure password-only authentication (no MFA):

![Authenticator No MFA](images/okta_15_authenticator_no_mfa.png)
![No MFA Rule](images/okta_16_no_mfa_rule.png)
![Authentication Policies](images/okta_17_auth_policies_list.png)
![Password Only Policy](images/okta_18_password_only_policy.png)
![Password Only Apps](images/okta_19_password_only_apps.png)

## Getting Started

The notebook `okta-auth-three-tier-end-to-end-demo.ipynb` walks through the full deployment:

1. Install dependencies and configure environment
2. Deploy MCP Server to AgentCore Runtime (Tier 3)
3. Deploy AgentCore Gateway with Interceptor Lambda (Tier 2)
4. Deploy Agent Runtime (Tier 1)
5. Test the full chain as Alice (full access) and Bob (limited access)
6. Verify token isolation
7. Cleanup all resources

## Project Structure

```
|-- okta-auth-three-tier-end-to-end-demo.ipynb   # Main deployment notebook
|-- mcp_server.py                # MCP Server with RBAC and security header middleware
|-- requirements.txt             # Python dependencies for MCP Server container
|-- images/                      # Architecture diagram and Okta setup screenshots
+-- .env.example                 # Environment variable template
```

Note: `agent_runtime/`, `Dockerfile`, `.dockerignore`, and `.bedrock_agentcore.yaml` are generated at deploy time by the starter toolkit and excluded from version control.

## Key Learnings

1. OAuth2 Credential Provider is for tool sync (control plane only)
2. Interceptor Lambda is for runtime token exchange (data plane)
3. `allowedAudience` must be configured in Gateway authorizer
4. `requestHeaderAllowlist` does not pass custom HTTP headers to the container -- use body `_meta` injection via the Interceptor instead
5. Target prefix must be short to keep tool names under 64 chars
6. `mcp.server.fastmcp.FastMCP` (built into the `mcp` SDK) is required -- not the separate `fastmcp` PyPI package
7. DPoP must be disabled on all Okta apps
8. `print()` in MCP tools goes to container stdout, not the client -- use tool return values to surface data
9. RBAC enforcement belongs in the MCP Server where the data lives -- propagate user identity via security headers

## Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [Okta Developer Docs](https://developer.okta.com/)
- [MCP Protocol Spec](https://modelcontextprotocol.io/)
- [OAuth2 RFC 6749](https://tools.ietf.org/html/rfc6749)
