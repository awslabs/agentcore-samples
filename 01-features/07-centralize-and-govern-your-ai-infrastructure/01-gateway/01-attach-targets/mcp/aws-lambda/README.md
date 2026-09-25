# MCPify your AWS Lambda with gateway OAuth Inbound

## Transform AWS Lambda functions into secure MCP tools with Bedrock AgentCore gateway

## Overview

Bedrock AgentCore gateway provides customers a way to turn their existing AWS Lambda functions into fully-managed MCP servers without needing to manage infra or hosting. gateway provides a uniform Model Context Protocol (MCP) interface across all these tools. gateway employs a dual authentication model to ensure secure access control for both incoming requests and outbound connections to target resources. The framework consists of two key components: Inbound Auth, which validates and authorizes users attempting to access gateway targets, and Outbound Auth, which enables the gateway to securely connect to backend resources on behalf of authenticated users. gateway uses IAM roles to authorize the calls to AWS Lambda functions for outbound authorization.

![How does it work](./images/architecture.png)

In this example, we will demonstrate OAuth for inbound authorization and IAM roles for outbound authorization.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `us.amazon.nova-pro-v1:0` (if using Strands demo)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy the Lambda Function (CloudFormation)

Deploy the sample Lambda function that the gateway will expose as MCP tools. The function provides two operations: `get_order_tool` and `update_order_tool`.

| Region    | Launch                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| :-------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| us-east-1 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=agentcore-gateway-lambda-sample&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/lambda/lambda-sample-stack.yaml) |
| us-west-2 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/new?stackName=agentcore-gateway-lambda-sample&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/lambda/lambda-sample-stack.yaml) |

Or deploy via the CLI:

```bash
export LAMBDA_STACK_NAME="agentcore-gateway-lambda-sample"

aws cloudformation deploy \
  --template-file cloudformation/lambda/lambda-sample-stack.yaml \
  --stack-name $LAMBDA_STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

Capture the Lambda function ARN:

```bash
export LAMBDA_STACK_NAME="agentcore-gateway-lambda-sample"

export LAMBDA_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$LAMBDA_STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey==\`LambdaFunctionArn\`].OutputValue" --output text)
echo "Lambda ARN: $LAMBDA_ARN"
```

## Step 3: Create AgentCore gateway (AgentCore CLI)

All tutorials share a single AgentCore CLI project at [`gatewaylabproject/`](../../../gatewaylabproject/). Navigate to that directory and run all subsequent CLI commands from there.

```bash
agentcore add gateway \
  --name lambda-oauth-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG
```

### Step 4: Create Lambda Target (AgentCore CLI)

The tool schema at [`tool-schemas/lambda-order-tools.json`](../../../gatewaylabproject/tool-schemas/lambda-order-tools.json) defines the two tools (`get_order_tool`, `update_order_tool`) and their input parameters.

```bash
agentcore add gateway-target \
  --name lambda-order-tools \
  --type lambda-function-arn \
  --lambda-arn $LAMBDA_ARN \
  --tool-schema-file tool-schemas/lambda-order-tools.json \
  --gateway lambda-oauth-gateway
```

### Step 5: Deploy (AgentCore CLI)

```bash
agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'lambda-oauth-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

Verify everything is running:

```bash
agentcore status
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to explore Lambda tools interactively.

![demo](./images/demo.gif)

### Option 1: AgentCore gateway MCP Client

```bash
uv sync
uv run python scripts/lambda-oauth/invoke.py
```

This lists all tools exposed through the gateway, then invokes `get_order_tool` and `update_order_tool`.

### Option 2: Strands Agents (optional)

![Strands agent calling gateway](images/strands-lambda-gateway.png)

```bash
uv run python scripts/lambda-oauth/strands_demo.py
```

The script connects a Strands Agent to the gateway and invokes tools via natural language:

```python
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

client = MCPClient(
    lambda: streamablehttp_client(
        gateway_url, headers={"Authorization": f"Bearer {token}"}
    )
)

model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

with client:
    tools = client.list_tools_sync()
    agent = Agent(model=model, tools=tools)
    agent("Check the order status for order id 123")
```

### Option 3: MCP SDK (optional)

```bash
uv run python scripts/lambda-oauth/mcp-invoke.py
```

The script uses the MCP Python SDK directly with streamable HTTP transport:

```python
from mcp.client.streamable_http import streamablehttp_client
from mcp.client import ClientSession

async with streamablehttp_client(
    gateway_url, headers={"Authorization": f"Bearer {token}"}
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print(tools)
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
agentcore remove gateway-target --name lambda-order-tools -y
agentcore remove gateway --name lambda-oauth-gateway -y
agentcore deploy --yes
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

Delete the Lambda stack:

```bash
aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Lambda as Target](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-lambda.html)
- [identity Provider Setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html)
