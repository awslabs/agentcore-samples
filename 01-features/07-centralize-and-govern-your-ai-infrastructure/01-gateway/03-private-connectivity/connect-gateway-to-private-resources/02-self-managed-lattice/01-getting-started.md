# Getting Started: Private API Gateway with Self-Managed VPC Lattice

This lab deploys the same private [Amazon API Gateway](https://docs.aws.amazon.com/apigateway/latest/developerguide/apigateway-private-apis.html) with mock integrations as the [managed VPC resource lab](../01-managed-vpc-resource/01-getting-started.md), but instead of letting AgentCore manage the VPC Lattice resources, **you create and manage the Resource Gateway and Resource Configuration yourself**.

### What's different from the managed VPC resource lab?

| | Managed | Self-Managed (this lab) |
|---|---------|----------------------|
| **Resource Gateway** | Created by AgentCore | You create it via `create_resource_gateway` |
| **Resource Configuration** | Created by AgentCore | You create it via `create_resource_configuration` |
| **CreateGatewayTarget** | `managedVpcResource` (VPC, subnets, SGs) | `selfManagedLatticeResource` (just the RC ARN) |
| **Cleanup** | Delete target only | Delete target, Resource Configuration, and Resource Gateway |
| **Control** | AgentCore manages lifecycle | You control subnet placement, SGs, IPs per ENI |

## Architecture

![arch](./images/api-gw.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- CDK dependencies installed (`npm install` at project root)

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root** (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Deploy Private API Gateway (CDK) — from project root

This is the same stack as the managed lab. If already deployed, this is a no-op.

```bash
cdk deploy PrivateApigw --require-approval never --outputs-file apigw-outputs.json
```

Capture outputs:

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

### Step 3: Create VPC Lattice Resource Gateway — from project root

In self-managed mode, **you** create the Resource Gateway. This provisions ENIs in the subnets you specify, serving as the entry point for AgentCore traffic into your VPC.

![rg](./images/resource-gateway.png)

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

export RESOURCE_GATEWAY_ID=$(aws vpc-lattice create-resource-gateway \
  --name self-managed-apigw-rg \
  --vpc-identifier $VPC_USW2_ID \
  --subnet-ids $(echo $VPC_USW2_PRIVATE_SUBNETS | tr ',' ' ') \
  --security-group-ids $VPCE_SG_ID \
  --ip-address-type IPV4 \
  --query 'id' --output text)

echo "Resource Gateway ID: $RESOURCE_GATEWAY_ID"
```

Wait for ACTIVE status:

```bash
while true; do
  STATUS=$(aws vpc-lattice get-resource-gateway \
    --resource-gateway-identifier $RESOURCE_GATEWAY_ID \
    --query 'status' --output text)
  echo "Status: $STATUS"
  [ "$STATUS" = "ACTIVE" ] && break
  sleep 15
done
```

### Step 4: Create Resource Configuration — from project root

The Resource Configuration defines **what** AgentCore is allowed to reach through the Resource Gateway — scoped to a single endpoint.

![rc](./images/resource-config.png)

```bash
export RESOURCE_CONFIG_ARN=$(aws vpc-lattice create-resource-configuration \
  --name self-managed-apigw-rc \
  --type SINGLE \
  --resource-gateway-identifier $RESOURCE_GATEWAY_ID \
  --resource-configuration-definition '{"dnsResource":{"domainName":"'$API_VPCE_DNS'","ipAddressType":"IPV4"}}' \
  --port-ranges '["443"]' \
  --query 'arn' --output text)

echo "Resource Configuration ARN: $RESOURCE_CONFIG_ARN"
```

Wait for ACTIVE:

```bash
export RESOURCE_CONFIG_ID=$(echo $RESOURCE_CONFIG_ARN | awk -F/ '{print $NF}')

while true; do
  STATUS=$(aws vpc-lattice get-resource-configuration \
    --resource-configuration-identifier $RESOURCE_CONFIG_ID \
    --query 'status' --output text)
  echo "Status: $STATUS"
  [ "$STATUS" = "ACTIVE" ] && break
  sleep 15
done
```

### Step 5: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name self-managed-apigw-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 6: Create AgentCore Gateway Target — from `vpcegress/`

Instead of providing VPC/subnet/SG details, you provide the **Resource Configuration ARN**. AgentCore associates it with its service network, completing end-to-end connectivity.

```bash
python3 scripts/create_target.py \
  --name self-managed-apigw \
  --endpoint $TARGET_ENDPOINT \
  --resource-gateway-arn $RESOURCE_CONFIG_ARN \
  --credential-provider-arn $(python3 -c "
import boto3
account = boto3.client('sts').get_caller_identity()['Account']
region = boto3.Session().region_name
print(f'arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default/apikeycredentialprovider/self-managed-apigw-api-key')
")
```

> [!NOTE]
> The `--resource-gateway-arn` flag in `create_target.py` maps to `privateEndpoint.selfManagedResources.resourceGatewayArn` in the API. For self-managed, this is the Resource Configuration ARN.

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 7: Invoke the API through AgentCore Gateway

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

Invoke health check:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"self-managed-apigw___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

## Cleanup

In self-managed mode, you must delete the VPC Lattice resources yourself (in reverse order).

> [!NOTE]
> When you delete the gateway target, AgentCore asynchronously removes the service network resource association. This can take a few minutes. If deleting the Resource Configuration fails with "has existing association", wait and retry.

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name self-managed-apigw
python3 scripts/delete_credential.py --name self-managed-apigw-api-key --type api-key
```

Delete VPC Lattice resources (from project root, wait a few minutes after target deletion):

```bash
aws vpc-lattice delete-resource-configuration \
  --resource-configuration-identifier $RESOURCE_CONFIG_ID

aws vpc-lattice delete-resource-gateway \
  --resource-gateway-identifier $RESOURCE_GATEWAY_ID
```

Destroy the API Gateway CDK stack (from project root):

```bash
cdk destroy PrivateApigw --force
```

> [!NOTE]
> The VPCE security group may be retained. Wait a few minutes, then:
> ```bash
> aws ec2 delete-security-group --group-id $VPCE_SG_ID
> ```

## Documentation

- [Self-Managed VPC Lattice](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress-self-managed.html)
- [VPC Lattice Resource Gateway](https://docs.aws.amazon.com/vpc-lattice/latest/ug/resource-gateways.html)
- [VPC Lattice Resource Configuration](https://docs.aws.amazon.com/vpc-lattice/latest/ug/resource-configurations.html)
