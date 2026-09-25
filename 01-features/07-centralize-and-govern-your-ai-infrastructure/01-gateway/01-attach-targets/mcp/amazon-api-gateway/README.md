# Integrate your API gateway as an AgentCore gateway Target

## Overview

As organizations explore the possibilities of agentic applications, they continue to navigate challenges of using enterprise data as context in invocation requests to large language models (LLMs) in a manner that is secure and aligned with enterprise policies. To help standardize and secure those interactions, many organizations are using the Model Context Protocol (MCP) specification, which defines how agentic applications can securely connect to data sources and tools.

![architecture](./images/architecture.png)

While MCP has been advantageous for net new use cases, organizations also navigate challenges with bringing their existing API estate into the agentic era. MCP can certainly wrap existing APIs, but it requires additional work, translating requests from MCP to RESTful APIs, making sure security is maintained through the entire request flow, and applying the standard observability required for production deployments.

[Amazon Bedrock AgentCore gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) supports [Amazon API Gateway](https://aws.amazon.com/api-gateway/) as a target, translating MCP requests into RESTful requests to API gateway. You can expose both new and existing API endpoints from API gateway to agentic applications via MCP, with built-in security and observability.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `us.anthropic.claude-sonnet-4-20250514-v1:0` (if using Strands demo)

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

### Step 2: Deploy Sample PetStore API (CloudFormation)

The sample PetStore REST API uses mock integrations with both IAM (SigV4) and API Key authorization patterns:

| Endpoint            | Method | Authorization | Description             |
| :------------------ | :----- | :------------ | :---------------------- |
| `/pets`             | GET    | IAM (SigV4)   | List all available pets |
| `/pets`             | POST   | IAM (SigV4)   | Add a new pet           |
| `/pets/{petId}`     | GET    | IAM (SigV4)   | Get pet by ID           |
| `/orders/{orderId}` | GET    | API Key       | Get order details       |

| Region    | Launch                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| :-------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| us-east-1 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=agentcore-petstore-api&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/api-gateway/petstore-api-stack.yaml) |
| us-west-2 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/new?stackName=agentcore-petstore-api&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/api-gateway/petstore-api-stack.yaml) |

Or deploy via the CLI:

```bash
export PETSTORE_STACK_NAME="agentcore-petstore-api"

aws cloudformation deploy \
  --template-file cloudformation/api-gateway/petstore-api-stack.yaml \
  --stack-name $PETSTORE_STACK_NAME \
  --no-fail-on-empty-changeset
```

Capture the outputs:

```bash
export API_ID=$(aws cloudformation describe-stacks \
  --stack-name $PETSTORE_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`RestApiId`].OutputValue' --output text)

export API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name $PETSTORE_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiKeyId`].OutputValue' --output text)

export API_KEY_VALUE=$(aws apigateway get-api-key \
  --api-key $API_KEY_ID --include-value \
  --query 'value' --output text)

echo "API ID:        $API_ID"
echo "API Key Value: $API_KEY_VALUE"
```

Wait 10-15 seconds for API gateway changes to propagate across availability zones.

### Step 3: Create AgentCore gateway (AgentCore CLI)

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
agentcore add gateway \
  --name apigw-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'apigw-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 4: Create gateway Targets (boto3)

API gateway does not preserve `operationId` in exported specs, so `toolOverrides` are required to name the tools. This script creates both targets:

1. **Target 1** (IAM auth): `/pets` GET/POST and `/pets/{petId}` GET — uses the gateway's IAM service role
2. **Target 2** (API Key auth): `/orders/{orderId}` GET — uses an API key stored in AgentCore identity

![filter](./images/agent-core-gateway-target.png)

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv run python scripts/api-gateway/deploy_targets.py
```

### Step 5: Verify Targets

```bash
aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier $GATEWAY_ID \
  --query 'items[].{name:name,status:status}' --output table
```

Ensure all targets are in `READY` state.

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to explore the API gateway tools interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
uv sync
uv run python scripts/api-gateway/invoke.py
```

This lists all tools exposed through the gateway, then invokes pet endpoints (IAM auth) and order endpoint (API Key auth).

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, delete targets and boto3-created resources first:

```bash
uv run python scripts/api-gateway/cleanup.py
```

Then remove the gateway (created via CLI):

```bash
agentcore remove gateway --name apigw-gateway -y
agentcore deploy --yes
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

Delete the PetStore API stack:

```bash
aws cloudformation delete-stack --stack-name $PETSTORE_STACK_NAME
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Summary

You have successfully integrated Amazon API Gateway with AgentCore gateway, exposing existing REST APIs as MCP-compatible endpoints for agentic applications. The integration supports IAM, API key, and no authorization patterns.

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [API gateway as Target](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-apigateway.html)
- [API gateway Usage Plans](https://docs.aws.amazon.com/apigateway/latest/developerguide/api-gateway-api-usage-plans.html)
