```mermaid
sequenceDiagram
    participant User as Gateway User
    participant Gateway as AgentCore Gateway
    participant Cred as AgentCore Identity Credential Provider
    participant IdP as OAuth 2.0 Authorization Server
    participant API as OpenAPI Schema Target

    Note over User, API: List tools (no auth required — served from cache)
    User->>Gateway: tools/list<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway-->>User: Cached tool definitions

    Note over User, API: Invoke tool (triggers OAuth flow for the target API)
    User->>Gateway: tools/call on target API<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway->>Cred: Get workload access token<br/>with workload identity and JWT
    Cred-->>Gateway: Return workload access token
    Gateway->>Cred: Request OAuth2 access token<br/>with workload access token
    Cred-->>Gateway: Authorization URL and session URI
    Gateway-->>User: Authorization URL and session URI

    User->>IdP: Sign in and authorize access
    IdP-->>Cred: Authorization code
    rect rgb(80, 80, 60)
        Note over User, IdP: Session Binding API
        Cred-->>User: Redirect to callback endpoint with Session URI
        User->>Cred: CompleteResourceTokenAuth with<br/>the JWT and session URI
        Cred->>IdP: Validate logged-in user with the user from the session URI,<br/>then request OAuth2 access token with authorization code
    end
    IdP-->>Cred: OAuth2 access token
    Cred->>Cred: Cache token in Token Vault<br/>under workload identity and user
    User->>Gateway: tools/call on target API<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway->>Cred: Get workload access token<br/>with workload identity and JWT
    Cred-->>Gateway: Return workload access token
    Gateway->>Cred: Request OAuth2 access token<br/>with workload access token
    Cred-->>Gateway: OAuth2 access token
    Gateway->>API: Call target API with access token
    API-->>Gateway: API response
    Gateway-->>User: Tool result
```
