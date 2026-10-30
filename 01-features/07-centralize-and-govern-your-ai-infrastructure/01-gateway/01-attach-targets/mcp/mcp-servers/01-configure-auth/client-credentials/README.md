# MCP Server Target with Client Credentials Auth

Attach a pre-existing MCP server to AgentCore gateway using OAuth 2.0 client credentials (M2M) for both inbound and outbound authentication. The gateway aggregates the server's tools, prompts, and resources into a single unified MCP endpoint.

![architecture](../../images/arcitecture.png)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `global.anthropic.claude-haiku-4-5-20251001-v1:0` (if using Strands demo)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../../00-optional-setup/).

Once the stack is deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

echo "Discovery URL:          $DISCOVERY_URL"
echo "MCP Client ID:          $MCP_CLIENT_ID"
echo "MCP Client Secret:      $MCP_CLIENT_SECRET"
echo "gateway Client ID:      $GATEWAY_CLIENT_ID"
echo "gateway Client Secret:  $GATEWAY_CLIENT_SECRET"
echo "Token Endpoint:         $TOKEN_ENDPOINT"
```

### Step 2: Deploy MCP Server (AgentCore CLI)

The MCP server code is at [`gatewaylabproject/app/labmcp/main.py`](../../../../../gatewaylabproject/app/labmcp/main.py). It exposes tools (`getOrder`, `updateOrder`), prompts (`order_summary_prompt`, `cancellation_email_prompt`), and resources (static and templated).

All tutorials share a single AgentCore CLI project at [`gatewaylabproject/`](../../../../../gatewaylabproject/). Navigate to that directory and run all subsequent CLI commands from there.

The `--client-id` and `--client-secret` flags create an OAuth credential provider automatically, which the gateway will use for outbound auth to this MCP server.

```bash
agentcore add agent \
  --name client_credentials_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/labmcp \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
```

### Step 3: Create AgentCore gateway (AgentCore CLI)

Create the gateway with inbound JWT auth:

The `--client-id` and `--client-secret` flags allow the CLI to fetch gateway bearer tokens for testing.

```bash
agentcore add gateway \
  --name client-credentials-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG
```

### Step 4: Deploy MCP Server and gateway (AgentCore CLI)

Deploy the MCP server and gateway:

```bash
agentcore deploy --yes
```

Capture the MCP server URL, gateway ID, and gateway URL:

```bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'client_credentials_mcp_server'))
")

export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'client-credentials-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "MCP Server URL: $MCP_SERVER_URL"
echo "gateway ID:     $GATEWAY_ID"
echo "gateway URL:    $GATEWAY_URL"
```

### Step 5: Create gateway Target (AgentCore CLI)

Create the gateway target pointing to the deployed MCP server. The `--credential-name` references the OAuth credential that was auto-created when registering the agent (`client_credentials_mcp_server-oauth`):

```bash
agentcore add gateway-target \
  --name client-credentials-mcp-server-target \
  --type mcp-server \
  --endpoint $MCP_SERVER_URL \
  --gateway client-credentials-gateway \
  --outbound-auth oauth \
  --credential-name client_credentials_mcp_server-oauth
```

### Step 6: Deploy gateway Target (AgentCore CLI)

```bash
agentcore deploy --yes
```

Verify everything is running:

```bash
agentcore status
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../../05-community/gateway-mcp-inspector/) to explore tools, prompts, and resources interactively.

### Option 1: AgentCore gateway MCP Inspector

Connect the [AgentCore gateway MCP Inspector](../../../../../05-community/gateway-mcp-inspector/) to your gateway:

1. Start the inspector by following [instructions](../../../../../05-community/gateway-mcp-inspector/).
2. Select your gateway from the gateway list, or paste the gateway URL
3. Under Authentication, select **Manual Token** and enter the Cognito JWT
4. Click **Connect** and explore tools, prompts, and resources

![demo](./images/demo.gif)

### Option 2: AgentCore gateway MCP Client

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory, install Python dependencies (first time only) and run the demo script:

```bash
uv sync
uv run python scripts/client-credentials/invoke.py
```

This calls `tools/list`, `tools/call`, `prompts/list`, `prompts/get`, `resources/list`, `resources/read`, and `resources/templates/list`.

### Option 3: Strands Agents (optional)

```bash
uv run python scripts/client-credentials/strands_demo.py
```

### Option 4: MCP SDK (optional)

```bash
uv run python scripts/client-credentials/mcp_invoke.py
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
agentcore remove gateway-target --name client-credentials-mcp-server-target -y
agentcore remove gateway --name client-credentials-gateway -y
agentcore remove agent --name client_credentials_mcp_server -y
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

Deploy to apply all removals:

```bash
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
- [identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
