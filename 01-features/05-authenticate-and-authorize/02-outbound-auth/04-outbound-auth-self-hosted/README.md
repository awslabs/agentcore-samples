# Self-Hosted Agent with AgentCore identity OAuth Token Management

| Information         | Details                                                                              |
|:--------------------|:-------------------------------------------------------------------------------------|
| Tutorial type       | Step-by-step                                                                         |
| Agent type          | Single (self-hosted, no AgentCore runtime)                                           |
| Agentic Framework   | None (standalone Python)                                                             |
| Tutorial components | AgentCore identity, Cognito user pool, local callback server                         |
| Example complexity  | Beginner                                                                             |
| SDK used            | boto3                                                                                |
| Credential Provider | Type: OAuth2 - Custom provider (Cognito)                                             |

## Overview

This tutorial demonstrates how to build a **self-hosted Python agent** that uses Amazon Bedrock
AgentCore identity to manage OAuth 2.0 token flows — without deploying to AgentCore runtime.

Instead of building OAuth authorization flows, token storage, refresh logic, and secret management
yourself, AgentCore identity handles all of that for you. Your agent only needs to:
1. Request a workload access token
2. Call `GetResourceOauth2Token`
3. Handle the browser redirect (via the local callback server in `agent.py`)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Local Machine                               │
│                                                                 │
│   ┌──────────────┐   GetWorkloadAccessToken   ┌─────────────┐  │
│   │   agent.py   │ ─────────────────────────► │  AgentCore  │  │
│   │              │                            │  identity   │  │
│   │              │ ◄───────────────────────── │             │  │
│   │              │    workload_access_token   │             │  │
│   │              │                            │             │  │
│   │              │   GetResourceOauth2Token   │             │  │
│   │              │ ─────────────────────────► │             │  │
│   │              │                            │             │  │
│   │              │ ◄───────────────────────── │             │  │
│   │              │    { authorizationUrl,     │             │  │
│   │              │      sessionUri }          │             │  │
│   │              │                            │             │  │
│   │  HTTP server │ ◄─── OAuth callback ────── │   Cognito   │  │
│   │  :8080       │      (from browser)        │  (OAuth     │  │
│   │              │                            │   server)   │  │
│   │              │   CompleteResourceTokenAuth │             │  │
│   │              │ ─────────────────────────► │             │  │
│   │              │                            │             │  │
│   │              │   GetResourceOauth2Token   │             │  │
│   │              │ ─────────────────────────► │             │  │
│   │              │ ◄───────────────────────── │             │  │
│   │              │    access_token ✓          │             │  │
│   └──────────────┘                            └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Concepts

- **Credential provider**: Tells AgentCore identity how to talk to your OAuth server
  (discovery URL, client ID/secret)
- **Workload identity**: Represents your agent. AgentCore issues workload tokens that
  your agent uses to request OAuth tokens
- **Session binding**: After the user authorizes in the browser, your app calls
  `CompleteResourceTokenAuth` to bind the OAuth session to the user
- **Workload access token**: A short-lived token that identifies both the agent and the user
- **Session URI**: Tracks the authorization flow state across requests

## Files

| File | Description |
|:-----|:------------|
| `self_hosted_agent_oauth.py` | Setup script: credential provider + workload identity |
| `agent.py` | The agent: requests OAuth tokens, opens browser, handles callback |
| `create_cognito.sh` | Shell script to create a Cognito user pool for testing |
| `requirements.txt` | Python dependencies |

## Prerequisites

- Python 3.10+
- AWS CLI configured with credentials
- `jq` installed: `brew install jq` (macOS) or `apt install jq` (Linux)
- Required permissions:
  - `bedrock-agentcore:CreateOauth2CredentialProvider`
  - `bedrock-agentcore:CreateWorkloadIdentity`
  - `bedrock-agentcore:GetResourceOauth2Token`
  - `bedrock-agentcore:CompleteResourceTokenAuth`
  - `secretsmanager:GetSecretValue` on `bedrock-agentcore*`
  - `cognito-idp:*` (if using create_cognito.sh)

## Setup

```bash
cd 02-outbound-auth/04-outbound-auth-self-hosted/

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Running the Scripts

### Step 1: Create an OAuth Authorization Server (Cognito)

If you already have an OAuth server with a client ID, client secret, and test user, skip to Step 2
and set the environment variables manually.

```bash
# Create a Cognito user pool (defaults to us-east-1; set AWS_REGION to change)
bash create_cognito.sh
```

Copy the values printed and export them:

```bash
export USER_POOL_ID="us-east-1_aBcDeFgHi"
export CLIENT_ID="1a2b3c4d5e6f7g8h9i0j"
export CLIENT_SECRET="abcdef123456..."
export ISSUER_URL="https://cognito-idp.us-east-1.amazonaws.com/<pool-id>/.well-known/openid-configuration"
export COGNITO_USERNAME="AgentCoreTestUser1234"
export COGNITO_PASSWORD="xYz...Aa1!"
```

### Step 2: Create Credential Provider + Workload identity

```bash
python self_hosted_agent_oauth.py
```

This creates:
- A `CustomOauth2` credential provider backed by your Cognito user pool
- A workload identity with `http://127.0.0.1:8080/callback` as the allowed return URL
- Updates Cognito to accept the AgentCore callback URL

### Step 3: Run the Agent

```bash
# Set CREDENTIAL_PROVIDER_NAME and WORKLOAD_NAME from the setup script output
export CREDENTIAL_PROVIDER_NAME="AgentCoreIdentityStandaloneProvider"
export WORKLOAD_NAME="standalone-agent-identity"
export AGENT_USER_ID="quickstart-user"

python3 agent.py
```

The agent opens your browser automatically. If it doesn't, copy the URL from the terminal.

## What to Expect

```
============================================================
  AgentCore identity - Local Agent
============================================================
[INFO]  App server listening on http://127.0.0.1:8080/callback
[INFO]  Workload identity 'standalone-agent-identity' exists - reusing.

  Opening your browser to authorize...

  https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/authorize?...

  Waiting for you to complete authorization in the browser...
[INFO]  Session bound for session_id=ZGQxN2ZlYjEtODcy...
[INFO]  Authorization callback received.

============================================================
  Access token retrieved!

  Your agent now has consent to act on behalf of the user.
  AgentCore identity handled the entire OAuth flow for you.
============================================================

  Token preview: eyJraWQiOiJxT0x0VGFhcVwiLCJhbGciOiJSUzI1...

{
  "sub": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "cognito:username": "AgentCoreTestUser1234",
  "token_use": "access"
}
```

## Troubleshooting

### `NoCredentialProviders` or `ExpiredToken`
**Issue**: AWS credentials are missing or expired.
**Solution**: Re-run `aws sso login` or configure credentials with `aws configure`.

### `ResourceNotFoundException` on workload identity
**Issue**: The workload identity was deleted or never created.
**Solution**: Re-run `python self_hosted_agent_oauth.py` to recreate it.

### Browser shows `error=redirect_mismatch`
**Issue**: The Cognito callback URLs don't include the AgentCore credential provider's callback URL.
**Solution**: Re-run `python self_hosted_agent_oauth.py` which calls `update_user_pool_client`
to add the callback URL.

### No access token received
**Issue**: The browser was closed before completing authorization.
**Solution**: Re-run `python3 agent.py` to get a fresh authorization URL.

### Port 8080 already in use
**Issue**: Another process is using port 8080.
**Solution**: Stop the other process or set `export CALLBACK_PORT=9090` and update the
workload identity's allowed return URLs.

### `InvalidParameterException` on `create-user-pool-domain`
**Issue**: The domain name is already taken.
**Solution**: Re-run `create_cognito.sh` — it generates a random suffix each time.

## Clean Up

```bash
python self_hosted_agent_oauth.py --cleanup
```

This deletes:
- Workload identity
- Credential provider
- Cognito user pool (if USER_POOL_ID is set in environment)
