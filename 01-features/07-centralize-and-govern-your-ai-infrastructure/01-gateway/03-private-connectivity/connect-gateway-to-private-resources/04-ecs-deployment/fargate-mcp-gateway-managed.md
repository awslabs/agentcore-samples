# Connecting Amazon ECS MCP Servers (Fargate) to AgentCore Gateway

Deploy a [FastMCP](https://github.com/jlowin/fastmcp) server on Amazon ECS Fargate inside a private VPC, then connect it to [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) using managed VPC egress with an internal ALB.

The MCP server is reachable via a **private domain** in a Route 53 private hosted zone associated with your VPC. With **Private DNS** enabled on the VPC (the default), AgentCore Gateway's managed Resource Gateway resolves the domain via your VPC's DNS resolver.

![arch](./images/ecs-fargate.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- Docker running (for CDK container image builds)
- An [ACM public certificate](../00-prerequisites/create-acm-public-certificate.md) for TLS termination on the ALB

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root** (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Configure domain and certificate — from project root

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="mcp.internal.yourcompany.com"

echo "Cert ARN: $CERT_ARN"
echo "Domain:   $DOMAIN"
```

### Step 2: Deploy MCP server on ECS Fargate (CDK) — from project root

This stack deploys:
- **ECS Fargate service** running the FastMCP server container (from `docker/fastmcp-mock/`)
- **Internal ALB** with your public ACM certificate for TLS termination on port 443
- **Route 53 private hosted zone** for `$DOMAIN` associated with the VPC, with an Alias record to the ALB
- **Security group** allowing inbound HTTPS from the VPC CIDR

```bash
cdk deploy McpEcs \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --require-approval never \
  --outputs-file ecs-outputs.json
```

Capture outputs:

```bash
export ALB_SG_ID=$(python3 -c "import json; print(json.load(open('ecs-outputs.json'))['McpEcs']['AlbSgId'])")
export MCP_ENDPOINT=$(python3 -c "import json; print(json.load(open('ecs-outputs.json'))['McpEcs']['McpEndpoint'])")

echo "MCP Endpoint: $MCP_ENDPOINT"
echo "ALB SG:       $ALB_SG_ID"
```

### Step 3: Create AgentCore Gateway Target — from `vpcegress/`

The target uses the private domain (resolves via Private DNS to the ALB's private IPs).

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name ecs-mcp-server \
  --endpoint $MCP_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids $ALB_SG_ID
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore MCP tools interactively.

### Step 4: Invoke the MCP server through AgentCore Gateway

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks --stack-name $COGNITO_STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)
export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks --stack-name $COGNITO_STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)
export USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name $COGNITO_STACK_NAME --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)
export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client --user-pool-id $USER_POOL_ID --client-id $GATEWAY_CLIENT_ID --query 'UserPoolClient.ClientSecret' --output text)

export ACCESS_TOKEN=$(curl -s -X POST $TOKEN_ENDPOINT \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$GATEWAY_CLIENT_ID&client_secret=$GATEWAY_CLIENT_SECRET&scope=api/gateway" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

List tools:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | python3 -m json.tool
```

Invoke the `echo` tool:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"ecs-mcp-server___echo","arguments":{"message":"Hello from ECS Fargate!"}},"id":2}' | python3 -m json.tool
```

Invoke the `add` tool:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"ecs-mcp-server___add","arguments":{"a":10,"b":32}},"id":3}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name ecs-mcp-server
```

Destroy the CDK stack (from project root):

```bash
cdk destroy McpEcs \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --force
```

> [!NOTE]
> The ALB security group may be retained. Wait a few minutes after target deletion, then:
> ```bash
> aws ec2 delete-security-group --group-id $ALB_SG_ID
> ```

## Documentation

- [Amazon ECS on Fargate](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/AWS_Fargate.html)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
- [FastMCP](https://github.com/jlowin/fastmcp)
