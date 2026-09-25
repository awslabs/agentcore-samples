# Connect to your AgentCore gateway using SigV4 Streamable HTTP

## Overview

AgentCore gateway supports AWS IAM as an inbound authorizer, allowing MCP clients to authenticate using AWS Signature V4 (SigV4) signed requests over Streamable HTTP. This eliminates the need for OAuth token management — clients use their existing AWS credentials to access gateway tools directly.

In this tutorial, we attach an AWS Lambda function as a gateway target and invoke its tools using SigV4-authenticated Streamable HTTP from the MCP Python SDK.

![How does it work](images/lambda-gw-iam-inbound.png)

In this scenrio, the MCP client or Agent calling the AgentCore gateway should have the following AWS IAM role.

![role](./images/iam-role.png)

### Tutorial Details

| Information          | Details                                    |
| :------------------- | :----------------------------------------- |
| Tutorial type        | Interactive                                |
| AgentCore components | AgentCore gateway                          |
| gateway Target type  | AWS Lambda                                 |
| Inbound Auth         | AWS IAM (SigV4 Streamable HTTP)            |
| Outbound Auth        | AWS IAM                                    |
| Example complexity   | Easy                                       |
| SDK used             | MCP Python SDK + custom SigV4 transport    |

### How SigV4 inbound auth works

1. The MCP client signs each HTTP request with AWS SigV4 credentials (access key, secret key, session token).
2. AgentCore gateway validates the signature and checks IAM permissions to authorize access.
3. For outbound connections to Lambda targets, the gateway uses its own IAM role to invoke the function.

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

### Step 1: Deploy the Lambda Function (CloudFormation)

Deploy the sample Lambda function that you want to expose as MCP tools. The Lambda function contains two operations: `get_order` and `update_order`.

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
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`LambdaFunctionArn`].OutputValue' --output text)
echo "Lambda ARN: $LAMBDA_ARN"
```

### Step 2: Create AgentCore gateway with IAM Inbound Auth (AgentCore CLI)

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, create a gateway using AWS IAM as the inbound authorizer (no Cognito needed):

```bash
agentcore add gateway \
  --name lambda-iam-gateway \
  --authorizer-type AWS_IAM \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'lambda-iam-gateway'))
")
echo "gateway ID: $GATEWAY_ID"

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp
echo "gateway URL: $GATEWAY_URL"
```

### Step 3: Create the Lambda Target (AgentCore CLI)

Attach the Lambda function as a gateway target:

```bash
agentcore add gateway-target \
  --name lambda-iam-target \
  --type lambda-function-arn \
  --lambda-arn $LAMBDA_ARN \
  --tool-schema-file tool-schemas/lambda-order-tools.json \
  --gateway lambda-iam-gateway

agentcore deploy --yes
```

### Step 4: Verify Deployment

```bash
agentcore status
```

Ensure all resources are in `READY` state.

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) with IAM inbound auth to explore Lambda tools interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory, install Python dependencies (first time only):

```bash
uv sync
```

### Invoke via MCP SDK (SigV4)

Lists tools and calls `get_order_tool` / `update_order_tool` using AWS SigV4 signing for inbound auth. Uses the MCP Python SDK with a custom SigV4 transport ([`streamable_http_sigv4.py`](../../../gatewaylabproject/streamable_http_sigv4.py)).

```bash
uv run python scripts/lambda-iam/invoke.py
```

### Support for AWS IAM Authentication in MCP Client SDKs

AWS IAM authentication is supported for inbound requests to AgentCore gateway. Current open-source MCP Client SDKs have limited support for SigV4 authentication, particularly for streamable HTTP connections. AWS has provided a solution through the [Run MCP servers with AWS Lambda](https://github.com/awslabs/run-model-context-protocol-servers-with-aws-lambda/tree/main) project, which includes a `StreamableHTTPTransportWithSigV4` class that extends the standard MCP transport layer to handle AWS SigV4 signing while maintaining streaming capabilities. This can be integrated with agentic frameworks like Strands or LangChain.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../gatewaylabproject/) directory:

```bash
agentcore remove gateway-target --name lambda-iam-target -y
agentcore remove gateway --name lambda-iam-gateway -y
agentcore deploy --yes
```

Delete the Lambda stack:

```bash
aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME
```
