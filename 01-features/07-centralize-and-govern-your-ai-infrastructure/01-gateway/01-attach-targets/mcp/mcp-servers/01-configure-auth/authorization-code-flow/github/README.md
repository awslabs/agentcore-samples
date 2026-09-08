# Connecting GitHub MCP Server to AgentCore gateway - self-hosted callback server

[GitHub's MCP server](https://github.com/github/github-mcp-server) exposes repository search, user lookup, workflow management, and more as MCP tools — but it requires OAuth authorization code flow for authentication. AgentCore gateway handles this complexity transparently: admin users authorize once during target creation, and all subsequent tool invocations reuse cached credentials.

This tutorial shows how to attach the GitHub MCP server to AgentCore gateway using:

- **Method 1** (Implicit sync): Admin completes the authorization code flow during target creation. gateway discovers and caches tools automatically.
- **Method 2** (Schema upfront): Admin provides the tool schema directly. No OAuth flow needed during creation — recommended for IaC pipelines.

Both methods enable gateway users to browse the full tool catalog without authenticating. The authorization code flow is only triggered when a user invokes a tool.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- A GitHub OAuth App ([create one here](https://github.com/settings/apps))

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`authorization-code-flow/`](../) directory — the parent of this one. Navigate there before proceeding: `cd ..`

### Step 1: Setup GitHub OAuth App

Create a [GitHub OAuth App](https://docs.github.com/en/apps/oauth-apps/using-oauth-apps) and note the Client ID and Client Secret. Export them as environment variables:

```bash
export GITHUB_CLIENT_ID="<your-github-client-id>"
export GITHUB_CLIENT_SECRET="<your-github-client-secret>"
```

### Step 2: Deploy the Cognito stack (gateway inbound auth)

The gateway authenticates its own callers with a Cognito user pool. This stack creates the pool, a machine-to-machine client for the gateway, and the OIDC discovery URL the gateway's `CUSTOM_JWT` authorizer validates against:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
aws cloudformation deploy \
  --template-file cloudformation/cognito-signup-stack.yaml \
  --stack-name "$COGNITO_STACK_NAME" \
  --capabilities CAPABILITY_IAM
```

> [!NOTE]
> If you already deployed this stack for another gateway tutorial, skip the deploy and just `export COGNITO_STACK_NAME` to its name. Steps 4 and the demo read the stack's outputs (`DiscoveryUrl`, `GatewayClientId`, `GatewayScope`, `TokenEndpoint`) rather than taking them as arguments.

### Step 3: Create GitHub Credential Provider

This creates the credential provider and outputs the callback URL you must register with your GitHub OAuth App:

```bash
uv run python scripts/deploy_credential.py --github
```

After running, update your GitHub App's **Authorization callback URL** with the URL printed by the script.

### Step 4: Create AgentCore gateway (boto3)

The gateway must advertise `2025-11-25` for URL-mode elicitation (authorization code flow).

```bash
uv run python scripts/deploy_gateway.py --github
```

The script creates the gateway with Cognito inbound auth, semantic search, response streaming enabled, gateway sessions enabled (1 hour timeout), and `supportedVersions: ["2025-11-25", "2025-06-18", "2025-03-26"]`. All three versions are advertised on purpose: `2025-11-25` is what enables the elicitation flow this tutorial demonstrates, and the two older ones let a client that does not speak it still connect rather than fail at negotiation. The script outputs the gateway ID and URL (also saved to `scripts/.env`).

Capture the gateway URL for the demo:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/.env | cut -d= -f2)
echo "gateway URL: $GATEWAY_URL"
```

### Step 5: Create gateway Target

Choose one method:

#### Method 1: Implicit sync (admin authorizes during creation)

**Terminal 1** — create the target (prints User ID and Authorization URL):

```bash
uv run python scripts/deploy_target_implicit.py --github
```

![wait](../images/need-auth.png)

**Terminal 2** — start the callback server with the User ID and Authorization URL from above. It opens the URL in your browser and waits for the redirect:

```bash
uv run python scripts/callback_server.py \
  --user-id "<User ID printed above>" \
  --auth-url "<Authorization URL printed above>"
```

Authorize GitHub in your browser. The callback server completes session binding automatically and exits. The target becomes `READY` with cached tools.

![ready](../images/complete-implicit.png)

#### Method 2: Schema upfront (no admin auth needed)

In this method [GitHub schema](./github.json) is provided. The script reads that same file — there is one copy, and it is the one linked here.

```bash
uv run python scripts/deploy_target_schema.py --github
```

![upfront](../images/complete-schema.png)

The target becomes immediately `READY`. Users will be prompted to authorize GitHub on their first tool invocation via URL-mode elicitation.

## Demo

> [!TIP]
> Use the [AgentCore gateway MCP Inspector](../../../../../../05-community/gateway-mcp-inspector/) to explore GitHub tools interactively. The Inspector handles the URL-mode elicitation flow (opens the authorization URL, completes session binding) automatically.

![demo](./images/demo.gif)

### Option 1: Invoke Script

**Terminal 1** — invoke the gateway (lists tools, calls `search_repositories`):

```bash
uv run python scripts/invoke.py --github
```

On first tool invocation, the script prints a URL elicitation with an Authorization URL and a session URI.

**Terminal 2** — start the callback server with the Cognito access token (for user-level session binding):

```bash
uv run python scripts/callback_server.py \
  --user-token "<cognito-access-token>" \
  --auth-url "<Authorization URL from invoke output>"
```

Authorize GitHub in your browser. The callback server completes session binding. Then run `invoke.py` again — the tool call succeeds with cached credentials.

![invoke](../images/invoke.png)

```json
{
  "error": {
    "code": -32042,
    "message": "This request requires more information.",
    "data": {
      "elicitations": [{
        "mode": "url",
        "url": "<authorization-url>",
        "message": "Please login to this URL for authorization."
      }]
    }
  }
}
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

Cleanup is two scripts, because targets and the gateway have different lifetimes — one gateway can front several MCP servers, so removing your targets should not be the same act as removing everyone's gateway.

First the targets and the credential provider:

```bash
uv run python scripts/cleanup_targets.py --github
```

Then, once nothing is attached, the gateway and its IAM role:

```bash
uv run python scripts/cleanup_gateway.py --github
```

> [!NOTE]
> `cleanup_targets.py --github` deletes only the targets the github profile created, and reports any others it left alone. `cleanup_gateway.py` refuses to run while any target is still attached, and tells you which ones. If you want everything on this gateway gone in one step, `cleanup_targets.py --all` deletes every target on it plus the credential provider of every profile in `scripts/servers/` — it prompts for confirmation first, and `--yes` skips the prompt. `--all` never touches credential providers belonging to other tutorials in your account.

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Authorization Code Flow](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-outbound-auth.html)
- [URL Session Binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html)
