# Outbound Authentication

Give your AgentCore agents secure, token-based access to external APIs — without ever
exposing credentials to the LLM context.

## Top-level layout

| Folder | Provider | Auth Pattern | What's inside |
|:-------|:---------|:-------------|:--------------|
| `01-outbound-auth-openai/` | OpenAI | API Key | runtime calls OpenAI via `@requires_api_key`; key stored in AgentCore identity vault |
| `02-outbound-auth-3lo/` | Google (Calendar) | Auth Code / 3LO | runtime calls Google Calendar via `@requires_access_token(auth_flow="USER_FEDERATION")`; includes consent URL flow |
| `03-outbound-auth-github/` | GitHub | Auth Code / 3LO | runtime calls GitHub API via `@requires_access_token(auth_flow="USER_FEDERATION")`; includes Cognito inbound auth |
| `04-outbound-auth-self-hosted/` | Custom Cognito OAuth2 | M2M client_credentials | runtime calls a self-hosted resource server via `CustomOauth2` credential provider |

## How this section is organized

Each sub-folder demonstrates a distinct outbound auth pattern supported by AgentCore identity.
The scripts show the full lifecycle: create credential provider → deploy runtime → invoke agent →
observe token injection → clean up.

## Auth Patterns

| Pattern | Grant Type | `auth_flow` value | User interaction? |
|:--------|:-----------|:------------------|:-----------------|
| **API Key** | N/A | N/A (`@requires_api_key`) | No |
| **M2M** | `client_credentials` | `"M2M"` | No |
| **3LO / Auth Code** | `authorization_code` | `"USER_FEDERATION"` | Yes — one-time consent URL |

## Key Concepts

| Concept | Details |
|:--------|:--------|
| `@requires_access_token` | Decorator that injects an OAuth2 access token into the wrapped function; token fetched from AgentCore identity vault |
| `@requires_api_key` | Decorator that injects a stored API key |
| Credential provider | Named configuration in AgentCore identity that holds the client ID, secret, and token endpoint for an OAuth2 server |
| Consent URL | On the first 3LO call, AgentCore identity returns a URL; user visits it, grants consent; subsequent calls return the cached token |
| `oauth2_callback_server.py` | Local FastAPI server (port 9090) that receives the OAuth redirect and calls `CompleteResourceTokenAuth` to bind the session |

## Prerequisites

- Python 3.10+
- AWS CLI configured with credentials
- `bedrock-agentcore` package: `pip install bedrock-agentcore`
- An active Cognito User Pool (for tutorials that use Cognito inbound auth)
- Credentials for the external API (OpenAI key, Google OAuth client, GitHub OAuth app, etc.)

## Running the Python Scripts

### 01-outbound-auth-openai

```bash
cd 01-outbound-auth-openai/
pip install -r requirements.txt
python outbound_auth_runtime.py
```

### 02-outbound-auth-3lo (Google Calendar)

```bash
cd 02-outbound-auth-3lo/
pip install -r requirements.txt

# Main demo script (deploys runtime + demonstrates consent flow)
python outbound_auth_3lo.py

# Optional: run the Cognito chatbot UI
python chatbot_app_cognito.py

# OAuth2 callback server (started automatically by outbound_auth_3lo.py, or run standalone)
python oauth2_callback_server.py
```

### 03-outbound-auth-github

```bash
cd 03-outbound-auth-github/
pip install -r requirements.txt

# Main demo script
python outbound_auth_github.py

# Optional: run the Cognito chatbot UI
python chatbot_app_cognito.py
```

### 04-outbound-auth-self-hosted

```bash
cd 04-outbound-auth-self-hosted/
pip install -r requirements.txt

# Step 1: create the Cognito resource server
bash create_cognito.sh

# Step 2: deploy + invoke the agent
python self_hosted_agent_oauth.py

# Cleanup
python self_hosted_agent_oauth.py --cleanup
```

## Resources

- [AgentCore identity documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)
- [Configuring outbound auth](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/outbound-auth.html)
- [OAuth 2.0 Authorization Code Flow](https://datatracker.ietf.org/doc/html/rfc6749#section-4.1)
- [OAuth 2.0 Client Credentials](https://datatracker.ietf.org/doc/html/rfc6749#section-4.4)
