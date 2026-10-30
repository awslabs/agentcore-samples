# AgentCore identity: M2M and Auth Code Flows with runtime (Cognito)

| Information         | Details                                                              |
|:--------------------|:---------------------------------------------------------------------|
| Tutorial type       | CLI walkthrough                                                      |
| Agent type          | Single                                                               |
| Agentic Framework   | Strands Agents                                                       |
| LLM model           | Anthropic Claude Haiku 4.5                                           |
| Tutorial components | AgentCore runtime, AgentCore identity, Amazon Cognito                |
| Inbound Auth        | Amazon Cognito (CUSTOM_JWT)                                          |
| Outbound Auth (M2M) | OAuth2 client credentials — `@requires_access_token(auth_flow="M2M")` |
| Outbound Auth (3LO) | OAuth2 auth code — `@requires_access_token(auth_flow="USER_FEDERATION")` |
| Example complexity  | Medium                                                               |
| CLI tool            | `agentcore` (npm: `@aws/agentcore`)                                  |

## Overview

This sample demonstrates two outbound OAuth2 flows in a single **AgentCore runtime** agent:

| Flow | Grant Type | Use Case |
|:-----|:-----------|:---------|
| **M2M** (machine-to-machine) | `client_credentials` | Agent calls internal/downstream APIs as itself — no user interaction |
| **Auth Code** (3LO) | `authorization_code` | Agent accesses user-owned resources (Google Calendar) — requires one-time user consent |

**Inbound Auth**: The runtime endpoint is protected by a Cognito JWT. Both flows require the
caller to present a valid bearer token.

## Architecture

```
Caller
  │  Authorization: Bearer <Cognito JWT>
  ▼
AgentCore runtime  ──validates JWT──▶  Cognito User Pool
  │
  ├─── M2M Tool ──@requires_access_token(auth_flow="M2M")──▶
  │              AgentCore identity (client credentials)    ──▶  Internal API
  │
  └─── 3LO Tool ──@requires_access_token(auth_flow="USER_FEDERATION")──▶
                 AgentCore identity (auth code)             ──▶  Google Calendar API
                         │
                         │ (first call only: returns consent URL)
                         ▼
                     User's browser ──consents──▶ Google ──callback──▶ localhost:9090
```

## Files

| File | Description |
|:-----|:------------|
| `app/MyAgent/main.py` | Agent code with M2M and 3LO tools |
| `setup_cognito.py` | Creates Cognito User Pool + machine client for M2M |
| `setup_oauth_providers.py` | Creates GitHub + Google credential providers in AgentCore identity |
| `configure_inbound_auth.py` | Post-deploy: attaches IAM permissions, KMS, registers callback URLs |
| `invoke.py` | Test script: invoke agent for M2M or 3LO flow |
| `oauth2_callback_server.py` | Local FastAPI server for OAuth2 session binding (port 9090) |
| `streamlit_app.py` | Interactive Streamlit UI for browser-based testing |
| `.env.example` | Template for environment variables |
| `images/` | Tutorial screenshots |

## Prerequisites

- **Node.js** 20.x or later
- **Python** 3.10+
- **uv** ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured
- **AgentCore CLI** installed:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

- **Amazon Bedrock model access**: Enable `claude-haiku-4-5` in the Bedrock console
- **For M2M**: An OAuth2 authorization server that supports `client_credentials` grant
- **For 3LO**: A Google Cloud project with Calendar API enabled (see Step 4)

## Step 1: Install Dependencies

```bash
cd 03-m2m-3lo/
pip install -r requirements.txt
```

## Step 2: Set Up Cognito (Inbound Auth)

```bash
python setup_cognito.py
```

Creates a Cognito User Pool and test user. Saves `cognito_config.json`.

Note the values printed for use in the deploy step:
```
--discovery-url    https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/openid-configuration
--allowed-clients  <client_id>
```

## Step 3: Create the AgentCore Project

```bash
agentcore create --name M2MAuthDemo --defaults --no-agent
cd M2MAuthDemo
```

Set your deployment target:

```bash
cat > agentcore/aws-targets.json << 'EOF'
[{"name":"default","description":"Default deployment target","account":"YOUR_AWS_ACCOUNT_ID","region":"us-east-1"}]
EOF
```

> Replace `YOUR_AWS_ACCOUNT_ID` with your 12-digit account ID:
> `aws sts get-caller-identity --query Account --output text`

## Step 4: Set Up OAuth Credential Providers

### 4a. Create a GitHub OAuth App

1. Go to GitHub > **Settings** > **Developer settings** > **OAuth Apps**
2. Click **New OAuth App** and fill in:
   - Application Name: any name
   - Homepage URL: your repo URL
   - Authorization callback URL: `https://bedrock-agentcore.us-east-1.amazonaws.com/identities/oauth2/callback/placeholder`
3. Copy the **Client ID** and generate a **Client Secret**
   <br><img src="images/github_details.png" width="60%">

### 4b. Create a Google OAuth App

1. Go to [Google Cloud Console](https://console.developers.google.com/) — create/select a project
2. Enable **Google Calendar API** (APIs & Services > Library)
3. Create OAuth consent screen (External audience, add your Gmail as test user)
4. Create Credentials > OAuth client ID > Web application
5. Copy **Client ID** and **Client Secret**
6. Add scope: `https://www.googleapis.com/auth/calendar.readonly`

### 4c. Create `.env` and run setup

```bash
# In the 03-m2m-3lo/ directory (not M2MAuthDemo/)
cp .env.example .env
# Edit .env with your values:
#   M2M_CLIENT_ID      = Cognito machine_client_id from cognito_config.json
#   M2M_CLIENT_SECRET  = Cognito machine_client_secret
#   M2M_DISCOVERY_URL  = Cognito discovery URL
#   GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET

python setup_oauth_providers.py
```

### 4d. Register callback URLs

**GitHub**: OAuth App Settings > Authorization callback URL → paste the GitHub callback URL printed by the script.

**Google**: Cloud Console > Credentials > your OAuth client > Authorised redirect URIs → add the Google callback URL printed by the script.

## Step 5: Add the Agent

```bash
cd M2MAuthDemo
agentcore add agent \
  --name MyAgent \
  --type byo \
  --code-location ../app/MyAgent \
  --entrypoint main.py \
  --language Python \
  --framework Strands \
  --model-provider Bedrock \
  --authorizer-type CUSTOM_JWT \
  --discovery-url YOUR_COGNITO_DISCOVERY_URL \
  --allowed-clients YOUR_COGNITO_CLIENT_ID
```

## Step 6: Deploy

```bash
agentcore deploy -y
```

## Step 7: Post-Deploy Configuration

```bash
cd ..
python configure_inbound_auth.py
```

Wait ~30 seconds for changes to propagate.

## Step 8: Test M2M Flow

```bash
python invoke.py --flow m2m
```

Expected output:
```
=== M2M Flow Test ===

Agent response:
The weather in Seattle is 47F, partly cloudy...
```

## Step 9: Test Auth Code (3LO) Flow

```bash
python invoke.py --flow authcode
```

First invocation — consent URL returned:
```
=== Auth Code (3LO) Flow Test ===
Starting OAuth2 callback server...

Agent response:
User authorization required. Please visit this URL and grant access:
https://accounts.google.com/o/oauth2/auth?...

Waiting for you to complete the Google consent flow...
After authorizing in your browser, press Enter to re-invoke the agent.
```

1. Click the URL → log in with Google → grant Calendar access
2. The callback server at `localhost:9090` handles the redirect
3. Press **Enter** to re-invoke

Second invocation — calendar events retrieved:
```
Agent response:
Calendar events for 2025-03-20:
  - 09:00: Standup
  - 14:00: Design Review
  - 16:30: 1:1 with Manager
```

## Streamlit UI (Optional)

```bash
pip install streamlit
streamlit run streamlit_app.py
```

Log in, select a flow (M2M / GitHub 3LO / Google 3LO), and use the chat interface.

## Key Concepts

| Concept | Details |
|:--------|:--------|
| **M2M (client credentials)** | `auth_flow="M2M"` — AgentCore identity calls the token endpoint with client ID + secret. No user interaction. Token cached per agent instance. |
| **Auth Code / 3LO** | `auth_flow="USER_FEDERATION"` — First call returns a consent URL. After consent, AgentCore identity stores and refreshes tokens automatically. |
| **Session binding** | `oauth2_callback_server.py` verifies the OAuth callback came from the same user who invoked the agent, preventing CSRF/session fixation attacks. |
| **Token storage** | All tokens stored in AgentCore identity (backed by Secrets Manager). Agent code only receives tokens in-memory via decorators. |

## Clean Up

```bash
cd M2MAuthDemo
agentcore remove agent --name MyAgent --force
agentcore remove credential --name M2MProvider --force
agentcore remove credential --name Google3LOProvider --force
```

Delete Cognito resources:
```python
import boto3, json

with open("../cognito_config.json") as f:
    config = json.load(f)

boto3.client("cognito-idp", region_name=config["region"]).delete_user_pool(
    UserPoolId=config["pool_id"]
)
print("Cognito User Pool deleted.")
```
