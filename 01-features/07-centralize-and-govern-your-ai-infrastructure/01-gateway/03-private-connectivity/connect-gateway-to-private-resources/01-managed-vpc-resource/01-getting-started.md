# Getting Started: Private API Gateway with Managed VPC Lattice

Connect a private [Amazon API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html) to [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) using managed VPC egress.

The [API-VPCE DNS format](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-api-create.html) (`{api-id}-{vpce-id}.execute-api.{region}.amazonaws.com`) is publicly resolvable with a valid AWS-managed TLS certificate. No domain name or ACM certificate is needed.

## Architecture

![arch](./images/api-gw.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- CDK dependencies installed (`npm install` at project root)

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the project root (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Deploy Private API Gateway (CDK) — from project root

This stack deploys:
- **Private API Gateway** with mock integrations (`/health` GET, `/items` GET/POST)
- **VPC Endpoint** for `execute-api` in private subnets with private DNS enabled
- **Security group** allowing inbound HTTPS (443) from the VPC CIDR

```bash
cdk deploy PrivateApigw --require-approval never --outputs-file apigw-outputs.json
```

Capture the stack outputs:

```bash
export API_ID=$(python3 -c "import json; print(json.load(open('apigw-outputs.json'))['PrivateApigw']['ApiId'])")
export API_KEY_ID=$(python3 -c "import json; print(json.load(open('apigw-outputs.json'))['PrivateApigw']['ApiKeyId'])")
export VPCE_ID=$(python3 -c "import json; print(json.load(open('apigw-outputs.json'))['PrivateApigw']['VpceId'])")
export VPCE_SG_ID=$(python3 -c "import json; print(json.load(open('apigw-outputs.json'))['PrivateApigw']['VpceSgId'])")

export API_VPCE_DNS="${API_ID}-${VPCE_ID}.execute-api.us-west-2.amazonaws.com"
export TARGET_ENDPOINT="https://${API_VPCE_DNS}/prod"

echo "API-VPCE DNS: $API_VPCE_DNS"
echo "Target endpoint: $TARGET_ENDPOINT"
```

### Step 2: Get the API Key value — from project root

```bash
export API_KEY_VALUE=$(aws apigateway get-api-key --api-key $API_KEY_ID --include-value --query 'value' --output text)
echo "API Key: ${API_KEY_VALUE:0:10}..."
```

### Step 3: Create API Key Credential Provider — from `vpcegress/`

AgentCore Gateway needs to authenticate to the API Gateway using an API key. Create an [API key credential provider](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-identity.html) that stores the key and tells AgentCore which header to send it in.

```bash
python3 scripts/create_credential.py \
  --name private-apigw-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 4: Create AgentCore Gateway Target — from `vpcegress/`

Create a gateway target with managed VPC egress. The endpoint uses the API-VPCE DNS format which is publicly resolvable. The `privateEndpoint.managedVpcResource` tells AgentCore to manage VPC Lattice resources automatically.

> **Security group:** The VPCE security group is passed to `securityGroupIds` so the Resource Gateway ENIs can reach the VPCE on port 443.

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name private-apigw \
  --endpoint $TARGET_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids $VPCE_SG_ID \
  --credential-provider-arn $CRED_PROVIDER_ARN
```

> [!NOTE]
> The target uses an OpenAPI schema. For full control over the schema, use the inline boto3 approach shown in the [notebook](./01-getting-started.ipynb).

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 5: Invoke the API through AgentCore Gateway

Get an access token and invoke the private API Gateway operations through the gateway as MCP tools:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

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

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export ACCESS_TOKEN=$(curl -s -X POST $TOKEN_ENDPOINT \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$GATEWAY_CLIENT_ID&client_secret=$GATEWAY_CLIENT_SECRET&scope=api/gateway" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "Token obtained."
```

List tools:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | python3 -m json.tool
```

Invoke health check:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"private-apigw___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

Invoke list items:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"private-apigw___listItems","arguments":{}},"id":3}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name private-apigw
```

Delete the API key credential provider:

```bash
python3 scripts/delete_credential.py --name private-apigw-api-key --type api-key
```

Destroy the API Gateway CDK stack (from project root):

```bash
cdk destroy PrivateApigw --force
```

> [!NOTE]
> The VPCE security group may be retained during stack deletion because VPC Lattice Resource Gateway ENIs still reference it. Wait a few minutes after target deletion, then delete it manually:
> ```bash
> aws ec2 delete-security-group --group-id $VPCE_SG_ID
> ```

## Documentation

- [Private API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
- [Managed VPC Resource](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress-managed.html)
