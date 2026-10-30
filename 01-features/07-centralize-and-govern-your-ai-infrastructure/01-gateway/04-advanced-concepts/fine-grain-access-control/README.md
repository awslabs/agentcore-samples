# Fine-Grained Access Control with AgentCore gateway Interceptors

## Overview

This tutorial demonstrates how to enforce **fine-grained access control (FGAC)** on AgentCore gateway using **interceptors** and **JWT scopes**. The interceptors inspect the user's OAuth scopes and restrict which tools they can invoke, see in `tools/list`, or discover via semantic search.

![architecture](images/FGAC_data_store.png)

Three access control patterns are demonstrated:

1. **Invoke tool with FGAC** (REQUEST interceptor) — block `tools/call` if the user's token doesn't include the required tool scope

  ![invoke](./images/invoke-tool.png)

2. **Semantic search with FGAC** (RESPONSE interceptor) — filter search results so users only see tools they have access to

  ![search](./images/search-tool.png)

3. **List tools with FGAC** (RESPONSE interceptor) — filter `tools/list` responses based on scopes

![list](./images/list-tool.png)

### Tutorial Details

| Information          | Details                                               |
| :------------------- | :---------------------------------------------------- |
| Tutorial type        | Interactive                                           |
| AgentCore components | AgentCore gateway, gateway Interceptors, runtime      |
| gateway Target type  | MCP Server (FastMCP on AgentCore runtime)             |
| Interceptor types    | AWS Lambda (REQUEST + RESPONSE)                       |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT) with per-tool scopes      |
| gateway features     | Semantic search enabled, dual interceptors            |
| Example complexity   | Intermediate                                          |
| SDK used             | boto3                                                 |

### How it works

The Cognito resource server defines scopes like:
- `fgac/fgac-mcp-target` — full access to all tools
- `fgac/fgac-mcp-target:getOrder` — access to `getOrder` only
- `fgac/fgac-mcp-target:cancelOrder` — access to `cancelOrder` only

The **REQUEST interceptor** base64-decodes the JWT payload (gateway already verified the signature), extracts the `scope` claim, and checks if the requested tool is allowed. If not, it returns a 403 error response.

The **RESPONSE interceptor** decodes the JWT from list/search responses and filters out tools the user isn't authorized to see.

The interceptor Lambda source is inline in the CloudFormation template: [`cloudformation/fine-grain-access-control/fgac-interceptors-stack.yaml`](../../gatewaylabproject/cloudformation/fine-grain-access-control/fgac-interceptors-stack.yaml)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

If you haven't deployed the shared Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/).

Once deployed, capture the outputs:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy FGAC Cognito Scopes (CloudFormation)

Add per-tool scopes to the shared Cognito pool:

```bash
export FGAC_COGNITO_STACK_NAME="agentcore-fgac-cognito"

aws cloudformation deploy \
  --template-file cloudformation/fine-grain-access-control/fgac-cognito-stack.yaml \
  --stack-name $FGAC_COGNITO_STACK_NAME \
  --parameter-overrides UserPoolId=$USER_POOL_ID \
  --no-fail-on-empty-changeset
```

Capture the FGAC client ID:

```bash
export FGAC_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $FGAC_COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`FGACClientId`].OutputValue' --output text)

export FGAC_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $FGAC_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

echo "FGAC Client ID: $FGAC_CLIENT_ID"
```

### Step 3: Deploy Interceptor Lambdas (CloudFormation)

```bash
export FGAC_LAMBDA_STACK_NAME="agentcore-fgac-interceptors"

aws cloudformation deploy \
  --template-file cloudformation/fine-grain-access-control/fgac-interceptors-stack.yaml \
  --stack-name $FGAC_LAMBDA_STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

Capture the interceptor ARNs:

```bash
export REQUEST_INTERCEPTOR_ARN=$(aws cloudformation describe-stacks \
  --stack-name $FGAC_LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`RequestInterceptorArn`].OutputValue' --output text)

export RESPONSE_INTERCEPTOR_ARN=$(aws cloudformation describe-stacks \
  --stack-name $FGAC_LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ResponseInterceptorArn`].OutputValue' --output text)

echo "Request Interceptor:  $REQUEST_INTERCEPTOR_ARN"
echo "Response Interceptor: $RESPONSE_INTERCEPTOR_ARN"
```

### Step 4: Register MCP Server (AgentCore CLI)

The MCP server exposes four tools: `getOrder`, `updateOrder`, `cancelOrder`, `deleteOrder`.

```bash
agentcore add agent \
  --name fgac_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/fine-grain-access-control \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET

agentcore deploy --yes
```

Capture the MCP server URL:

```bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'fgac_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 5: Create gateway with Interceptors (boto3)


```bash
uv run python scripts/fine-grain-access-control/deploy.py
```

Export the gateway URL:

```bash
source scripts/fine-grain-access-control/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../05-community/gateway-mcp-inspector/) to test FGAC by requesting tokens with different scopes.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
uv sync
uv run python scripts/fine-grain-access-control/invoke.py
```

### Test Cases

![fgac](./images/fgac.png)

| Test | Scope requested | Tool called | Expected |
| :--- | :--- | :--- | :--- |
| 1 | `fgac/fgac-mcp-target:getOrder` | `getOrder` | ALLOW |
| 2 | `fgac/fgac-mcp-target:getOrder` | `updateOrder` | DENY (403) |
| 3 | `fgac/fgac-mcp-target:deleteOrder` | `deleteOrder` | ALLOW |
| 4 | `fgac/fgac-mcp-target:deleteOrder` | `getOrder` | DENY (403) |
| 5 | `fgac/fgac-mcp-target` (full) | all tools | ALLOW all |
| 6 | `fgac/fgac-mcp-target:getOrder` | `tools/list` | Only `getOrder` visible |
| 7 | `fgac/fgac-mcp-target` (full) | `tools/list` | All 4 tools visible |
| 8 | `fgac/fgac-mcp-target:getOrder` | semantic search | Only `getOrder` in results |
| 9 | `fgac/fgac-mcp-target` (full) | semantic search | All relevant tools in results |

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
uv run python scripts/fine-grain-access-control/cleanup.py
```

Delete the gateway and IAM role:

```bash
uv run python scripts/cleanup_gateway.py \
  --name fgac-gateway \
  --env-file scripts/fine-grain-access-control/.env
```

Remove the CLI-managed agent:

```bash
agentcore remove agent --name fgac_mcp_server -y
agentcore deploy --yes
```

Delete the CloudFormation stacks:

```bash
aws cloudformation delete-stack --stack-name $FGAC_LAMBDA_STACK_NAME
aws cloudformation delete-stack --stack-name $FGAC_COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
