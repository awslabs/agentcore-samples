# Preventing SQL Injection Attacks with AgentCore gateway Interceptors

## Overview

AgentCore gateway interceptors allow you to inspect and block tool calls before they reach your targets. This tutorial demonstrates how to use a **REQUEST interceptor** with a Lambda function that detects SQL injection patterns in tool arguments using regex-based pattern matching.

When an MCP client calls a database tool through the gateway, the interceptor analyzes tool arguments for SQL injection indicators (stacked queries, UNION SELECT, tautologies, time-based injection, SQL comments). Malicious requests are blocked before they reach the database tool.

![SQL Injection Prevention Architecture](images/sql-injection-prevention.png)

### Tutorial Details

| Information          | Details                                            |
| :------------------- | :------------------------------------------------- |
| Tutorial type        | Interactive                                        |
| AgentCore components | AgentCore gateway, gateway Interceptors            |
| gateway Target type  | AWS Lambda (mock database tool)                    |
| Interceptor type     | AWS Lambda (REQUEST)                               |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)                        |
| Example complexity   | Intermediate                                       |
| SDK used             | boto3                                              |

### How it works

1. MCP client calls a tool (e.g., `customer_query_tool`) through the gateway
2. The REQUEST interceptor Lambda receives the tool call before the target
3. The interceptor scans all string arguments for SQL injection patterns
4. If a pattern matches: request is blocked with a generic error (no attack details exposed)
5. If clean: request proceeds to the target Lambda

The interceptor Lambda source is inline in the CloudFormation template: [`cloudformation/sql-injection/sql-injection-stack.yaml`](../../gatewaylabproject/cloudformation/sql-injection/sql-injection-stack.yaml)

### SQL injection patterns detected

- **Statement stacking**: `;DROP TABLE`, `;DELETE FROM`
- **SQL comments**: `--`, `/*`, `*/`
- **UNION SELECT**: data exfiltration attempts
- **Tautologies**: `OR 1=1`, `AND 1=1`
- **Time-based injection**: `SLEEP()`, `WAITFOR DELAY`, `BENCHMARK()`

> [!NOTE]
> This demo uses heuristic pattern matching. In production, the recommended control is to disallow raw SQL and require parameterized queries or structured query templates.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- IAM permissions for Lambda, IAM, Cognito, and Bedrock AgentCore

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy Lambda Functions (CloudFormation)

Deploy the interceptor Lambda (SQL injection detection) and the mock customer query tool Lambda:

| Region    | Launch |
| :-------- | :----- |
| us-east-1 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=agentcore-sql-injection-lambdas&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/sql-injection/sql-injection-stack.yaml) |
| us-west-2 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/new?stackName=agentcore-sql-injection-lambdas&templateURL=https://raw.githubusercontent.com/awslabs/private-amazon-bedrock-agentcore-samples-staging/main/02-features/05-centralize-and-govern-your-ai-infrastructure/gateway/gatewaylabproject/cloudformation/sql-injection/sql-injection-stack.yaml) |

Or deploy via the CLI from the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
export LAMBDA_STACK_NAME="agentcore-sql-injection-lambdas"

aws cloudformation deploy \
  --template-file cloudformation/sql-injection/sql-injection-stack.yaml \
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

### Step 3: Create AgentCore gateway with REQUEST Interceptor (boto3)

> [!NOTE]
> The AgentCore CLI does not yet support `interceptorConfigurations`. This step uses a boto3 script.

```bash
uv run python scripts/prevent-sql-injection/deploy.py
```

Export the gateway URL (saved by the deploy script):

```bash
source scripts/prevent-sql-injection/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../05-community/gateway-mcp-inspector/) to test SQL injection patterns interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory, install Python dependencies (first time only):

```bash
uv sync
```

### Step 4: Test SQL injection prevention

The invoke script obtains a Cognito token and runs test cases: a legitimate query (allowed), a stacked query injection (blocked), a UNION SELECT injection (blocked), and a tautology injection (blocked).


```bash
uv run python scripts/prevent-sql-injection/invoke.py
```

**Expected output:**

```text
Test 1: Legitimate Query (Should PASS)
  Query passed — customer data returned

Test 2: SQL Injection - Stacked Query (Should BLOCK)
  Blocked — category: SQL_INJECTION_DETECTED

Test 3: SQL Injection - UNION SELECT (Should BLOCK)
  Blocked — category: SQL_INJECTION_DETECTED

Test 4: SQL Injection - Tautology (Should BLOCK)
  Blocked — category: SQL_INJECTION_DETECTED
```

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
uv run python scripts/prevent-sql-injection/cleanup.py
```

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
- [gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors.html)
