# Token Passthrough — Forwarding Client Authorization to Targets

## Overview

This tutorial demonstrates how to pass through the client's `Authorization` token to downstream targets using a REQUEST interceptor. Since the `Authorization` header cannot be allowlisted in `metadataConfiguration`, the only way to forward it is via an interceptor Lambda that includes it in the `transformedGatewayRequest.headers` response.

> [!CAUTION]
> **Token passthrough is an anti-pattern in the MCP specification.** The [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices#token-passthrough) explicitly state that MCP servers MUST NOT accept tokens that were not issued for them. Passing a client's token directly to a downstream server bypasses audience validation, breaks accountability/audit trails, circumvents security controls (rate limiting, request validation), and creates trust boundary issues. **Use this pattern only for Lambda targets or internal services where you control both sides and accept the tradeoffs.** For MCP server targets, prefer the gateway's credential provider (which issues a properly-scoped token for the target) over token passthrough.

This tutorial demonstrates the mechanism for educational purposes. In production, evaluate whether your use case genuinely requires the original user's token at the target, or whether the gateway's credential provider with on-behalf-of (OBO) token exchange is more appropriate.

Two target types are demonstrated:
- **Lambda target**: receives the passthrough token in propagated headers
- **MCP server target** (DYNAMIC mode): receives the token as the `Authorization` header on every request, and demonstrates live tool listing without caching

![Token passthrough](../images/token-passthrough.png)

### What this tutorial demonstrates

1. **Authorization token passthrough via interceptor** — interceptor extracts the client's Bearer token and passes it through to targets
2. **Interceptor overrides credential provider** — when the interceptor provides `Authorization`, it takes precedence over the gateway's outbound credential provider token
3. **Lambda target receives passthrough token** — accessible in `context.client_context.custom['bedrockAgentCorePropagatedHeaders']['Authorization']`
4. **MCP server target with DYNAMIC listing** — `listingMode='DYNAMIC'` means every `tools/list` is forwarded live to the MCP server (no caching)
5. **DEFAULT vs DYNAMIC comparison** — same MCP server attached as both modes to compare cached vs live listing

### Tutorial Details

| Information          | Details                                                    |
| :------------------- | :--------------------------------------------------------- |
| Tutorial type        | Interactive                                                |
| AgentCore components | AgentCore gateway, gateway Interceptors, runtime           |
| gateway Target type  | AWS Lambda + MCP Server (DEFAULT + DYNAMIC)                |
| Interceptor type     | AWS Lambda (REQUEST)                                       |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)                                |
| Outbound Auth        | OAuth (MCP server, DEFAULT mode), passthrough (DYNAMIC)    |
| Example complexity   | Intermediate                                               |
| SDK used             | boto3                                                      |

### How it works

1. Client sends request with `Authorization: Bearer <client-jwt>`
2. gateway validates the JWT (inbound auth)
3. REQUEST interceptor extracts the `Authorization` header and includes it in the pass-through response
4. For the **Lambda target**: gateway invokes Lambda with the client's token in propagated headers
5. For the **MCP server target (DYNAMIC)**: gateway forwards the request with the interceptor's `Authorization` header, overriding the credential provider's token
6. For the **MCP server target (DEFAULT)**: `tools/list` is served from cache (no token passthrough needed for listing)

### DEFAULT vs DYNAMIC listing

| Aspect | DEFAULT | DYNAMIC |
| :--- | :--- | :--- |
| `tools/list` | Served from gateway cache | Forwarded live to MCP server |
| `tools/call` | Live to MCP server | Live to MCP server |
| Requires sync after tool changes | Yes | No |
| Compatible with semantic search | Yes | No |
| Outbound auth on list | Not needed (cached) | Token required |

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

### Step 2: Deploy Interceptor + Tool Lambda (CloudFormation)

Deploy the token-passthrough interceptor Lambda and an echo tool Lambda:

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
export LAMBDA_STACK_NAME="agentcore-token-passthrough-lambdas"

aws cloudformation deploy \
  --template-file cloudformation/header-query-propagation/token-passthrough-stack.yaml \
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

The MCP server echoes back the Authorization header it receives, demonstrating the token passthrough.

```bash
agentcore add agent \
  --name token_passthrough_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/token-passthrough \
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
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'token_passthrough_mcp_server'))
")
echo "MCP Server URL: $MCP_SERVER_URL"
```

### Step 4: Create gateway + Targets (boto3)

This script creates:
- gateway with the token-passthrough REQUEST interceptor
- Lambda target with `GATEWAY_IAM_ROLE` credential type
- MCP server target in DEFAULT mode (with OAuth credential provider)
- MCP server target in DYNAMIC mode (with OAuth credential provider)

```bash
uv run python scripts/header-query-propagation/token-passthrough/deploy.py
```

Export the gateway URL:

```bash
source scripts/header-query-propagation/token-passthrough/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to test token passthrough interactively.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv sync
uv run python scripts/header-query-propagation/token-passthrough/invoke.py
```

### Test Cases

| Test | What it verifies |
| :--- | :--- |
| 1 | Lambda target receives the client's original Bearer token (not the gateway's credential provider token) |
| 2 | MCP server target (DEFAULT) serves `tools/list` from cache (no live call) |
| 3 | MCP server target (DYNAMIC) forwards `tools/list` live to MCP server |
| 4 | MCP server target receives the client's token on `tools/call` (interceptor passthrough) |
| 5 | Adding a tool to the MCP server: DEFAULT stays stale, DYNAMIC shows it immediately |

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv run python scripts/header-query-propagation/token-passthrough/cleanup.py
```

Delete the gateway and IAM role:

```bash
uv run python scripts/cleanup_gateway.py \
  --name token-passthrough-gateway \
  --env-file scripts/header-query-propagation/token-passthrough/.env
```

Remove the CLI-managed agent:

```bash
agentcore remove agent --name token_passthrough_mcp_server -y
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
- [Dynamic Listing Mode](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-mcp.html)
