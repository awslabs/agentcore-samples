# AgentCore Identity: Runtime Inbound and Outbound Auth (Cognito)

## Overview

This sample shows how to secure an **AgentCore Runtime** agent with both inbound and outbound authentication using Amazon Cognito as the Identity Provider (IdP).

- **Inbound Auth**: The runtime endpoint is protected by a Cognito JWT. Callers must present a valid bearer token or receive `AccessDeniedException`.
- **Outbound Auth**: The agent retrieves an API key from AgentCore Identity (backed by AWS Secrets Manager) at runtime. The key is never stored in environment variables or agent code.

### Architecture

```
Caller
  │  Authorization: Bearer <Cognito JWT>
  ▼
AgentCore Runtime  ──validates JWT──▶  Cognito User Pool
  │
  │  @requires_api_key("OutboundApiKey")
  ▼
AgentCore Identity  ──fetches secret──▶  AWS Secrets Manager
  │
  ▼
External API (weather service, OpenAI, etc.)
```

### Tutorial Details

| Information         | Details                                               |
|:--------------------|:------------------------------------------------------|
| Tutorial type       | CLI walkthrough                                       |
| Agent type          | Single                                                |
| Agentic Framework   | Strands Agents                                        |
| LLM model           | Anthropic Claude Haiku 4.5                            |
| Inbound Auth        | Amazon Cognito (CUSTOM_JWT)                           |
| Outbound Auth       | AgentCore Identity - API Key credential provider      |
| Example complexity  | Easy                                                  |
| CLI tool            | `agentcore` (npm: `@aws/agentcore`)                   |

---

## Prerequisites

- **Node.js** 20.x or later
- **Python** 3.10+
- **uv** ([install](https://docs.astral.sh/uv/getting-started/installation/))
- **AWS credentials** configured (`aws configure` or environment variables)
- **AgentCore CLI** installed:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

- **Amazon Bedrock model access**: Enable `claude-haiku-4-5` in the [Bedrock console](https://console.aws.amazon.com/bedrock/home#/models)

---

## Step 1: Install Setup Dependencies

```bash
pip install -r requirements.txt
```

---

## Step 2: Set Up Cognito (Inbound IdP)

```bash
python setup_cognito.py
```

This creates:
- A Cognito User Pool with one test user (`testuser` / `AgentCoreTest1!`)
- An App Client with `USER_PASSWORD_AUTH` enabled
- Saves pool ID, client ID, and discovery URL to `cognito_config.json`

Take note of the two values printed at the end — you will need them in Step 4:

```
--discovery-url    https://cognito-idp.<region>.amazonaws.com/<pool_id>/.well-known/openid-configuration
--allowed-clients  <client_id>
```

---

## Step 3: Create the AgentCore Project

```bash
agentcore create --name RuntimeAuthDemo --defaults --no-agent
cd RuntimeAuthDemo
```

Set your deployment target (the CLI creates an empty `aws-targets.json`):

```bash
cat > agentcore/aws-targets.json << 'EOF'
[{"name":"default","description":"Default deployment target","account":"YOUR_AWS_ACCOUNT_ID","region":"us-east-1"}]
EOF
```

> Replace `YOUR_AWS_ACCOUNT_ID` with your 12-digit AWS account ID. Find it with `aws sts get-caller-identity --query Account --output text`.

---

## Step 4: Add the Agent (Bring Your Own Code)

Use the `--authorizer-type CUSTOM_JWT` flags to configure inbound JWT auth at deploy time. Replace the placeholder values with the discovery URL and client ID from Step 2:

```bash
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

---

## Step 5: Add Outbound Identity Credential

The agent calls the [OpenWeatherMap API](https://openweathermap.org/api) which requires an API key. Get a free one:

1. Sign up at [openweathermap.org](https://home.openweathermap.org/users/sign_up) (free tier)
2. Go to [API keys](https://home.openweathermap.org/api_keys) and copy your key

Store it securely in AgentCore Identity:

```bash
agentcore add credential \
  --name OutboundApiKey \
  --api-key YOUR_OPENWEATHERMAP_API_KEY
```

> The CLI stores the key in AWS Secrets Manager via AgentCore Identity. At runtime, the agent retrieves it with `@requires_api_key("OutboundApiKey")`. The key never appears in code or environment variables.

---

## Step 6: Deploy

```bash
agentcore deploy -y
```

Deployment takes a few minutes. Monitor progress:

```bash
agentcore status
```

---

## Step 7: Test Inbound and Outbound Auth

Go back to the sample root directory and run the invoke script:

```bash
cd ..
python invoke.py "What is the weather in Seattle?"
```

The script runs two tests:

1. **Without bearer token** — expects `AccessDeniedException`
2. **With valid Cognito bearer token** — expects a successful agent response

Expected output:

```
[Test 1] Invoking WITHOUT bearer token (expect AccessDeniedException)...
  Correctly rejected: An error occurred (AccessDeniedException) ...

[Test 2] Invoking WITH valid Cognito bearer token...
  Token obtained (first 20 chars): eyJraWQiOiJxT...

Agent response:
The weather in Seattle is currently Sunny, 72F.
```

---

## Streamlit UI (Optional)

For an interactive browser-based experience instead of the CLI:

```bash
pip install streamlit
cd ..
streamlit run streamlit_app.py
```

Log in, then use the chat interface to test weather queries. Clear the Bearer Token field in the sidebar to test 403 rejection.

---

## Step 8: Cleanup

```bash
cd RuntimeAuthDemo
agentcore remove agent --name MyAgent --force
agentcore remove credential --name OutboundApiKey --force
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

---

## Key Concepts

| Concept | How it works in this sample |
|:--------|:---------------------------|
| **Inbound JWT validation** | AgentCore Runtime checks `Authorization: Bearer <token>` against the Cognito JWKS endpoint before executing the agent |
| **Outbound API key** | `@requires_api_key(provider_name="OutboundApiKey")` calls `bedrock-agentcore:GetResourceApiKey` + `secretsmanager:GetSecretValue` at runtime |
| **Zero-secret agent code** | API keys live in Secrets Manager; agent code only sees them in-memory via the decorator |
