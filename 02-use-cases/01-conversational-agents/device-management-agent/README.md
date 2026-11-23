# Give an assistant a read-only device inventory tool

An assistant supporting a sandbox fleet can look up a device's model and firmware without being able to reconfigure it. This CloudFormation example creates a JWT-authenticated **AgentCore Gateway**, one MCP Lambda target, the narrowly scoped roles and a seven-day Lambda log group. The tool reads one item in an **existing** DynamoDB table. No Runtime deployment or model inference is required.

## Before you deploy

- Use a dedicated sandbox account and Region with AgentCore Gateway. Review the template and charges. Use temporary AWS credentials and verify the caller with `aws sts get-caller-identity`.
- Bring a table in that account and Region with a string partition key `device_id` and **no sort key**. Use synthetic inventory only. Example DynamoDB item: `{"device_id":{"S":"demo-01"},"model":{"S":"lab-router"},"firmware_version":{"S":"1.2.3"}}`. The sample does not create, seed, scan, update or delete the table.
- Bring an operator-approved Cognito user pool in the same Region, an app client and a dedicated resource-server scope such as `fleet/read`. The Gateway checks issuer via the pool's discovery URL, allowed client ID and required scope. Use your authorized client flow to obtain a short-lived **access token**. Do not use an ID token, commit app secrets, or place tokens on command lines. No pool, users or clients are created by this stack.
- **Authorization granularity:** a caller with this client and scope can look up **any known device ID in the table**. This is a single-fleet service boundary, not per-user or per-tenant authorization. Don't point it at a mixed-tenant or production inventory.

## Create and try the tool

Set `REGION`, `STACK`, `GATEWAY_NAME`, `TABLE`, `POOL`, `CLIENT` and `SCOPE` to your sandbox values. Use a **new stack name** so the tutorial cannot update an existing stack. Save the returned full stack ID.

```bash
STACK_ID=$(aws cloudformation create-stack --region "$REGION" --stack-name "$STACK" \
  --template-body file://inventory.yaml --capabilities CAPABILITY_IAM \
  --parameters ParameterKey=GatewayName,ParameterValue="$GATEWAY_NAME" \
               ParameterKey=TableName,ParameterValue="$TABLE" \
               ParameterKey=UserPoolId,ParameterValue="$POOL" \
               ParameterKey=AppClientId,ParameterValue="$CLIENT" \
               ParameterKey=ReadScope,ParameterValue="$SCOPE" \
  --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$REGION" --stack-name "$STACK_ID"
aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK_ID" \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayUrl`].OutputValue' --output text
```

Configure your MCP client's secure OAuth/bearer mechanism for **that specific Gateway URL**. List tools, select `inventory___get_device`, and send `{"device_id":"demo-01"}`. A match returns `found: true` and only the device ID, model and firmware. A miss returns `{"found":false}`. A request cannot select another action, table or DynamoDB attribute. The function does not log events or tokens. The Lambda role grants `GetItem` on one table; the Gateway role can invoke only the stack's function.

## Cleanup

Verify the account, Region and full stack ID. Then delete that stack and wait:

```bash
aws cloudformation delete-stack --region "$REGION" --stack-name "$STACK_ID"
aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$STACK_ID"
```

The table and OAuth resources remain under their original ownership. If you previously deployed the [historical device management application](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/02-use-cases/01-conversational-agents/device-management-agent), reconcile those resources separately; the new stack does not adopt them. This narrower workflow does not replace the old frontend, agent conversations, user/activity queries or Wi-Fi mutations. For a hosted agent with a UI, see the [customer-support assistant](../customer-support-assistant-vpc/).
