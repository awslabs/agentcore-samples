# MCP Dynamic Client Registration with Auth0

## Overview

[Dynamic Client Registration (DCR)](https://datatracker.ietf.org/doc/html/rfc7591) allows MCP clients to register themselves with an OAuth authorization server at runtime, without pre-configured client credentials. This eliminates the need to manually create OAuth app registrations for every client that connects to your MCP server.

This example deploys an MCP server to AgentCore runtime and integrates it with [Auth0](https://auth0.com/) as the OAuth provider. When a client connects, it:

1. Discovers the Auth0 OAuth endpoints via `.well-known/openid-configuration`
2. Registers itself as a new OAuth client via the DCR endpoint
3. Redirects the user to Auth0 for authorization
4. Exchanges the authorization code for an access token
5. Connects to the MCP server with the token

## How DCR Works with MCP

The MCP specification supports OAuth-based authentication. When a server requires auth, the client follows the standard OAuth flow. DCR adds an extra step at the beginning — the client registers itself before starting the authorization flow.

```
Client                    Auth0                     MCP Server (AgentCore)
  │                         │                              │
  │── GET /.well-known/ ──→│                              │
  │←── OAuth metadata ─────│                              │
  │                         │                              │
  │── POST /register ─────→│  (DCR: create client)       │
  │←── client_id/secret ───│                              │
  │                         │                              │
  │── GET /authorize ─────→│  (user logs in)              │
  │←── authorization_code ──│                              │
  │                         │                              │
  │── POST /token ────────→│                              │
  │←── access_token ────────│                              │
  │                         │                              │
  │── MCP request + token ─────────────────────────────→  │
  │←── MCP response ───────────────────────────────────── │
```

### Auth0 Configuration

Before deploying, configure your Auth0 tenant:

1. **Enable DCR** — In Auth0 Dashboard → Settings → Advanced → enable "OIDC Dynamic Application Registration"
2. **Create an API** — Define an API identifier (audience) that the MCP server will validate
3. **Set permissions** — Add any scopes your MCP tools require
4. **Configure callback URLs** — Allow `http://localhost:3030/callback` for the local client

### The `audience` Parameter

Auth0 requires the `audience` parameter in authorization requests to identify which API's token settings to use. Without it, Auth0 returns opaque tokens instead of JWTs. The client adds this parameter automatically:

```python
def add_auth0_audience_parameter(authorization_url: str, audience: str) -> str:
    """Add Auth0 'audience' parameter to the authorization URL."""
    parsed = urlparse(authorization_url)
    query_params = parse_qs(parsed.query)
    query_params['audience'] = [audience]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                       parsed.params, urlencode(query_params, doseq=True),
                       parsed.fragment))
```

## What This Example Demonstrates

### MCP Server (`server.py`)

A simple MCP server with three tools, deployed with `MCP` protocol:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)

@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b

@mcp.tool()
def multiply_numbers(a: int, b: int) -> int:
    """Multiply two numbers together"""
    return a * b

@mcp.tool()
def greet_user(name: str) -> str:
    """Greet a user by name"""
    return f"Hello, {name}! Nice to meet you."
```

### Auth0 MCP Client (`mcp_auth0_client.py`)

A client that handles the full OAuth + DCR flow:

- Starts a local callback server on port 3030
- Uses `OAuthClientProvider` from the MCP SDK for token management
- Opens the browser for user authorization
- Connects via `streamable-http` transport with the access token

## Files

| File | Description |
|:-----|:------------|
| `server.py` | MCP server with three demo tools |
| `mcp_auth0_client.py` | Client with Auth0 OAuth + DCR flow |
| `deploy.py` | Deploys the MCP server to AgentCore runtime |
| `invoke.py` | Explains how to invoke (requires Auth0 OAuth flow) |
| `cleanup.py` | Deletes runtime, endpoint, S3 artifact, IAM role |
| `requirements.txt` | Python dependencies |

## Quick Start

```bash
# 1. Configure Auth0 environment variables
export AUTH0_DOMAIN="your-tenant.auth0.com"
export AUTH0_AUDIENCE="your-api-identifier"

# 2. Deploy the MCP server
python deploy.py

# 3. Set the runtime ARN for the client
export AGENT_ARN="<runtime_arn from deploy output>"

# 4. Run the Auth0 MCP client (opens browser for OAuth)
python mcp_auth0_client.py

# 5. Clean up
python cleanup.py
```

## Prerequisites

- Auth0 account with DCR enabled
- Python 3.12+
- AWS CLI configured with appropriate credentials
- Browser access for the OAuth authorization flow
