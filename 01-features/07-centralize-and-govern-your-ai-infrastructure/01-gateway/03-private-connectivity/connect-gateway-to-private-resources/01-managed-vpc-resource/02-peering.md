# VPC Peering: Connect to a Private API in Another Region

Connect Amazon Bedrock AgentCore Gateway to a **Private API Gateway in a peered VPC** in a different region. This is a common enterprise pattern where services are deployed across multiple regions or VPCs for isolation, compliance, or proximity.

## Architecture

![peering](./images/peering.png)

### How it works

1. A **Private API Gateway** with a VPC Endpoint runs in the peered VPC (us-east-1, 10.1.0.0/16)
2. A **VPC Peering connection** links the two VPCs across regions (us-west-2 <-> us-east-1)
3. Route table entries in both VPCs direct cross-VPC traffic through the peering connection
4. AgentCore Gateway creates a **managed Resource Gateway** in the owner VPC (us-west-2, 10.0.0.0/16)
5. The Resource Gateway ENI resolves the API-VPCE DNS to the VPCE's private IPs (10.1.x.x) and routes traffic through the peering connection

The API-VPCE DNS format (`{api-id}-{vpce-id}.execute-api.us-east-1.amazonaws.com`) is **publicly resolvable** — it resolves to the VPCE's private IPs.

> **Why does this work?** Interface VPC Endpoints (powered by AWS PrivateLink) create ENIs with private IP addresses. These IPs are routable through VPC peering connections, unlike Gateway VPC Endpoints (S3/DynamoDB) which are route-table-based and not accessible through peering.

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC us-west-2 + AgentCore Gateway deployed)
- **us-east-1 stacks deployed**: In Lab 0, deploy the us-east-1 VPC + API Gateway + VPC peering stacks
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the project root (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Verify us-east-1 infrastructure — from project root

The following stacks should have been deployed in [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md):

| Stack | Description |
|-------|-------------|
| `VpcegressStack-USEast1` | VPC (10.1.0.0/16) with public, private, and isolated subnets |
| `PeeringApigw-USEast1` | Private API Gateway with mock integrations + execute-api VPC Endpoint |
| `VpcPeeringStack` | VPC peering connection + route table entries |

Capture the outputs:

```bash
export PEERING_API_ID=$(aws cloudformation describe-stacks \
  --stack-name PeeringApigw-USEast1 --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiId`].OutputValue' --output text)

export PEERING_VPCE_ID=$(aws cloudformation describe-stacks \
  --stack-name PeeringApigw-USEast1 --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`VpceId`].OutputValue' --output text)

export PEERING_API_KEY_ID=$(aws cloudformation describe-stacks \
  --stack-name PeeringApigw-USEast1 --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiKeyId`].OutputValue' --output text)

export API_VPCE_DNS="${PEERING_API_ID}-${PEERING_VPCE_ID}.execute-api.us-east-1.amazonaws.com"
export TARGET_ENDPOINT="https://${API_VPCE_DNS}/prod"

echo "API-VPCE DNS:    $API_VPCE_DNS"
echo "Target endpoint: $TARGET_ENDPOINT"
```

### Step 2: Get the API Key value

```bash
export API_KEY_VALUE=$(aws apigateway get-api-key \
  --api-key $PEERING_API_KEY_ID --include-value --region us-east-1 \
  --query 'value' --output text)
echo "API Key: ${API_KEY_VALUE:0:10}..."
```

### Step 3: Create security group for the Resource Gateway — from project root

The Resource Gateway ENIs are placed in the us-west-2 VPC and need outbound HTTPS access through the peering connection. New security groups allow all outbound by default.

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")

export RG_SG_ID=$(aws ec2 create-security-group \
  --group-name "peering-rg-sg-$(date +%s)" \
  --description "Resource Gateway SG for peering lab" \
  --vpc-id $VPC_USW2_ID \
  --query 'GroupId' --output text)
echo "Resource Gateway SG: $RG_SG_ID"
```

### Step 4: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name peering-apigw-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 5: Create AgentCore Gateway Target — from `vpcegress/`

The Resource Gateway is placed in the **us-west-2 VPC** (owner VPC), and the target endpoint is the API-VPCE DNS in **us-east-1** (peered VPC).

Traffic flow:
```text
AgentCore Gateway -> VPC Lattice -> Resource Gateway ENI (us-west-2)
    -> VPC Peering -> VPCE ENI (us-east-1) -> Private API Gateway
```

```bash
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name peering-apigw \
  --endpoint $TARGET_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids $RG_SG_ID \
  --credential-provider-arn $(python3 -c "
import boto3
account = boto3.client('sts').get_caller_identity()['Account']
region = boto3.Session().region_name
print(f'arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default/apikeycredentialprovider/peering-apigw-api-key')
")
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 6: Invoke the API through AgentCore Gateway

Get an access token and invoke the Private API Gateway in us-east-1 through the gateway. Traffic flows from us-west-2 through the VPC peering connection.

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

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

Invoke health check (reaches Private API Gateway in us-east-1 via VPC peering):

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"peering-apigw___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

List items:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"peering-apigw___listItems","arguments":{}},"id":3}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name peering-apigw
python3 scripts/delete_credential.py --name peering-apigw-api-key --type api-key
```

Delete the Resource Gateway security group (wait a few minutes after target deletion for ENIs to release):

```bash
aws ec2 delete-security-group --group-id $RG_SG_ID
```

> [!NOTE]
> The VPC peering connection, routes, and CDK stacks (`VpcPeeringStack`, `PeeringApigw-USEast1`, `VpcegressStack-USEast1`) are destroyed in [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) cleanup.

## Documentation

- [VPC Peering](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html)
- [Private API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
