# Connecting LinkedIn to AgentCore gateway with Authorization Code Flow

## Overview

This tutorial connects LinkedIn APIs to AgentCore gateway using OAuth 2.0 authorization code grant for outbound authentication. The gateway exposes LinkedIn's userinfo endpoint as an MCP tool, enabling agents to access user profile information on behalf of authenticated users.

AgentCore gateway handles the OAuth complexity transparently: the authorization code flow is only triggered when a user invokes a tool — users can browse the tool catalog without authenticating to LinkedIn first.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A LinkedIn OAuth App ([create one here](https://developer.linkedin.com/))

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Setup LinkedIn OAuth App

1. Go to [developer.linkedin.com](https://developer.linkedin.com/) and create an app
2. In the **Products** section, enable "Sign In with LinkedIn using OpenID Connect"
3. Note the **Client ID** and **Client Secret** from the Auth section

```bash
export LINKEDIN_CLIENT_ID="<your-linkedin-client-id>"
export LINKEDIN_CLIENT_SECRET="<your-linkedin-client-secret>"
export COGNITO_STACK_NAME="agentcore-gateway-lab"
```

### Step 2: Create LinkedIn Credential Provider

```bash
uv run python scripts/linkedin-auth-code/deploy_credential.py
```

After running, update your LinkedIn App's **Authorized redirect URLs** (under OAuth 2.0 settings) with the callback URL printed by the script.

### Step 3: Create AgentCore gateway (boto3)

The gateway requires `supportedVersions: ["2025-11-25"]` for URL-mode elicitation:

```bash
uv run python scripts/linkedin-auth-code/deploy_gateway.py
```

### Step 4: Create LinkedIn Target

```bash
uv run python scripts/linkedin-auth-code/deploy_target.py
```

The target becomes immediately `READY` (schema provided upfront). Users will be prompted to authorize LinkedIn on first tool invocation via URL-mode elicitation.

### Step 5: Verify

Capture the gateway URL:

```bash
export GATEWAY_URL=$(cat scripts/linkedin-auth-code/.env | grep GATEWAY_URL | cut -d= -f2)
echo "gateway URL: $GATEWAY_URL"
```

## Demo

> [!TIP]
> Use the [AgentCore gateway MCP Inspector](../../../../../../05-community/gateway-mcp-inspector/) to explore LinkedIn tools interactively.

![demo](./images/demo.gif)

**Terminal 1** — invoke the gateway:

```bash
uv run python scripts/linkedin-auth-code/invoke.py
```

On first tool invocation, the gateway returns a URL elicitation prompting LinkedIn authorization.

**Terminal 2** — start the callback server with your Cognito access token:

```bash
uv run python scripts/linkedin-auth-code/callback_server.py \
  --user-token "<cognito-access-token>" \
  --auth-url "<Authorization URL from invoke output>"
```

Authorize LinkedIn in your browser. The callback server completes session binding. Run `invoke.py` again — the tool call succeeds with cached credentials.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

```bash
uv run python scripts/linkedin-auth-code/cleanup.py
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Authorization Code Flow](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
- [URL Session Binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
