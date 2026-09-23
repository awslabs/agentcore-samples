```mermaid
sequenceDiagram
    participant User as Gateway User
    participant Gateway as AgentCore Gateway
    participant Cred as AgentCore Identity Credential Provider
    participant IdP as OAuth 2.0 Authorization Server
    participant MCP as MCP Server (Target)

    Note over User, MCP: List tools (no auth required — served from cache)
    User->>Gateway: list/tools<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway-->>User: Cached tool definitions

    Note over User, MCP: Invoke tool (triggers OAuth flow for the specific MCP server)
    User->>Gateway: Invoke tool on MCP server<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway->>Cred: Get workload access token<br/>with workload identity and JWT
    Cred-->>Gateway: Return workload access token
    Gateway->>Cred: Request GitHub OAuth2 access token<br/>with workload access token
    Cred-->>Gateway: GitHub authorization URL and session URI
    Gateway-->>User: GitHub authorization URL and session URI

    User->>IdP: Sign in to GitHub and authorize access
    IdP-->>Cred: GitHub authorization code
    rect rgb(80, 80, 60)
        Note over User, IdP: Session Binding API
        Cred-->>User: Redirect to callback endpoint with Session URI
        User->>Cred: CompleteResourceTokenAuth with<br/>the JWT and session URI
        Cred->>IdP: Validate logged-in user with the user from the session URI,<br/>then request GitHub OAuth2 access token with authorization code
    end
    IdP-->>Cred: GitHub OAuth2 access token
    Cred->>Cred: Cache token in Token vault<br/>under workload identity and user
    User->>Gateway: Invoke tool on MCP server<br/>Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
    Gateway->>Cred: Get workload access token<br/>with workload identity and JWT
    Cred-->>Gateway: Return workload access token
    Gateway->>Cred: Request GitHub OAuth2 access token<br/>with workload access token
    Cred-->>Gateway: GitHub OAuth2 access token
    Gateway->>MCP: Invoke tool
    MCP-->>Gateway: Tool result
    Gateway-->>User: Tool result
```
