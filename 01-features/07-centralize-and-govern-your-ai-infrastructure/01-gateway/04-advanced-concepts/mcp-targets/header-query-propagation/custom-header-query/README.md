# Custom Header and Query Parameter Propagation with Interceptor Precedence

## Overview

This tutorial demonstrates how to propagate custom HTTP headers and query parameters from clients to targets using `metadataConfiguration`, combined with a REQUEST interceptor that shows header precedence rules. The gateway forwards only allowlisted headers to targets, and interceptor-provided headers override client-provided ones.

Two target types are demonstrated side-by-side:
- **Lambda target**: receives propagated headers in `context.client_context.custom['bedrockAgentCorePropagatedHeaders']`
- **MCP server target** (DEFAULT mode): receives propagated headers as HTTP headers on the outbound request

![Architecture](../images/allowlist.png)

### What this tutorial demonstrates

1. **Allowlisted header propagation** — only headers in `metadataConfiguration.allowedRequestHeaders` reach the target
2. **Query parameter propagation** — query params in `allowedQueryParameters` are forwarded
3. **Response header propagation** — response headers in `allowedResponseHeaders` are returned to the client
4. **Interceptor precedence** — interceptor-provided headers override client-provided ones for the same name
5. **Non-allowlisted interceptor headers are dropped** — even if the interceptor returns a header, it must be in the allowlist to reach the target (except `Authorization`)

![Header override](../images/header-override.png)

### Tutorial Details

| Information          | Details                                                 |
| :------------------- | :------------------------------------------------------ |
| Tutorial type        | Interactive                                             |
| AgentCore components | AgentCore gateway, gateway Interceptors                 |
| gateway Target type  | AWS Lambda + MCP Server (DEFAULT mode)                  |
| Interceptor type     | AWS Lambda (REQUEST)                                    |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)                             |
| Outbound Auth        | AWS IAM (Lambda), OAuth (MCP server)                    |
| Example complexity   | Intermediate                                            |
| SDK used             | boto3                                                   |

### How it works

1. Client sends request with custom headers (`x-correlation-id: abc123`, `x-tenant-id: A`) and query params (`?version=v1`)
2. gateway checks the target's `metadataConfiguration` allowlist
3. REQUEST interceptor receives the request, overrides `x-tenant-id` to `B`, adds `x-custom-tenant-id: C`
4. gateway forwards to target: `x-correlation-id: abc123` (client, unchanged), `x-tenant-id: B` (interceptor override), but NOT `x-custom-tenant-id` (not in allowlist)
5. Target returns response with `x-rate-limit-remaining: 95`
6. gateway checks `allowedResponseHeaders` and forwards `x-rate-limit-remaining` to client

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../00-optional-setup/).

Once deployed, capture the outputs:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy Interceptor + Tool Lambdas (CloudFormation)

Deploy the REQUEST interceptor Lambda and an echo tool Lambda:

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
export LAMBDA_STACK_NAME="agentcore-header-query-lambdas"

aws cloudformation deploy \
  --template-file cloudformation/header-query-propagation/custom-header-query-stack.yaml \
  --stack-name $LAMBDA_STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

Capture the Lambda ARNs:

```bash
export INTERCEPTOR_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`InterceptorFunctionArn`].OutputValue' --output text)

export TOOL_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ToolFunctionArn`].OutputValue' --output text)

echo "Interceptor ARN: $INTERCEPTOR_ARN"
echo "Tool ARN:        $TOOL_ARN"
```

### Step 3: Register MCP Server (AgentCore CLI)

The MCP server echoes back any propagated headers it receives.

```bash
agentcore add agent \
  --name header_echo_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/header-query-propagation \
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
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'header_echo_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 4: Create gateway + Targets (boto3)

This script creates the gateway with the REQUEST interceptor, a Lambda target, and an MCP server target — both with `metadataConfiguration` allowlists.

```bash
uv run python scripts/header-query-propagation/custom-header-query/deploy.py
```

Export the gateway URL:

```bash
source scripts/header-query-propagation/custom-header-query/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to test header propagation interactively.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv sync
uv run python scripts/header-query-propagation/custom-header-query/invoke.py
```

### Test Cases

| Test | What it verifies |
| :--- | :--- |
| 1 | Allowlisted headers (`x-correlation-id`, `x-tenant-id`) reach the Lambda target |
| 2 | Query params (`version`, `environment`) reach the Lambda target |
| 3 | Interceptor overrides `x-tenant-id` from client value to interceptor value |
| 4 | Non-allowlisted header from interceptor (`x-custom-tenant-id`) is dropped |
| 5 | Response header (`x-rate-limit-remaining`) is returned to client |
| 6 | Same propagation works for the MCP server target |

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv run python scripts/header-query-propagation/custom-header-query/cleanup.py
```

Delete the gateway and IAM role:

```bash
uv run python scripts/cleanup_gateway.py \
  --name header-query-gateway \
  --env-file scripts/header-query-propagation/custom-header-query/.env
```

Remove the CLI-managed agent:

```bash
agentcore remove agent --name header_echo_mcp_server -y
agentcore deploy --yes
```

Delete the CloudFormation stack:

```bash
aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME
```

## Documentation

- [Header Propagation with gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-headers.html)
- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
