# Private Certificate Authority: ALB Proxy Solution

Connect [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) to a backend that uses a **private certificate** (from AWS Private CA or self-signed). AgentCore Gateway validates TLS certificates against public root CAs only — it cannot connect directly to endpoints with private certificates.

## Solution

**Simplest fix:** Replace the private certificate with a public ACM certificate. If that's not possible (you don't own the domain, or the cert comes from a private CA you can't change), deploy an **internal ALB with a public certificate** in front of your backend.

## Architecture

![arch](./images/private-ca.png)

### Traffic flow

1. Set the **target URL** to a domain matching your public ACM certificate (e.g., `https://api.external.yourcompany.com`)
2. Set **`routingDomain`** to the proxy ALB DNS name (publicly resolvable)
3. VPC Lattice routes traffic to the ALB via the routing domain. TLS SNI matches the public cert — handshake succeeds
4. The ALB **terminates TLS** and applies a **host header transform** (rewrites `Host` from public domain to private domain)
5. The ALB **forwards to your backend over HTTPS** using the private certificate. All traffic stays inside the VPC

> [!NOTE]
> The ALB does not validate the backend's private certificate when forwarding over HTTPS. This is standard ALB behavior.

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- An [ACM public certificate](../00-prerequisites/create-acm-public-certificate.md) for a domain you own

> [!WARNING]
> This lab deploys an AWS Private CA ($50/month). Delete it during cleanup to avoid ongoing charges.

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root** (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Deploy the sample backend with Private CA cert — from project root

This deploys a short-lived Private CA and an EC2 instance with a private certificate + NLB:

```bash
cdk deploy ShortLivedPrivateCa --require-approval never
cdk deploy PrivateCaBackend --require-approval never --outputs-file backend-outputs.json
```

Capture outputs:

```bash
export BACKEND_IP=$(python3 -c "import json; print(json.load(open('backend-outputs.json'))['PrivateCaBackend']['Ec2PrivateIp'])")
export CERT_DOMAIN=$(python3 -c "import json; print(json.load(open('backend-outputs.json'))['PrivateCaBackend']['CertDomain'])")
export NLB_DNS=$(python3 -c "import json; print(json.load(open('backend-outputs.json'))['PrivateCaBackend']['NlbDnsName'])")
export API_KEY_VALUE=$(python3 -c "import json; print(json.load(open('backend-outputs.json'))['PrivateCaBackend']['ApiKey'])")

echo "Backend IP:   $BACKEND_IP"
echo "Cert Domain:  $CERT_DOMAIN"
echo "NLB DNS:      $NLB_DNS"
```

### Step 2: Configure your public certificate — from project root

Provide an ACM public certificate ARN for a domain you own. This can be any domain — it does not need to match the backend's domain.

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="api.external.yourcompany.com"
```

### Step 3: Deploy the public cert proxy ALB — from project root

The proxy ALB terminates TLS with your public cert and forwards to the backend over HTTPS with a host header transform.

```bash
cdk deploy PublicCertProxy \
  -c "publicCertArn=$CERT_ARN" \
  -c "publicDomain=$DOMAIN" \
  -c "backendIp=$BACKEND_IP" \
  -c "backendDomain=$CERT_DOMAIN" \
  --require-approval never \
  --outputs-file proxy-outputs.json
```

Capture the proxy ALB DNS (this becomes the `routingDomain`):

```bash
export PROXY_ALB_DNS=$(python3 -c "import json; print(json.load(open('proxy-outputs.json'))['PublicCertProxy']['AlbDnsName'])")
export PROXY_ALB_SG_ID=$(python3 -c "import json; print(json.load(open('proxy-outputs.json'))['PublicCertProxy']['AlbSgId'])")

echo "Proxy ALB DNS (routingDomain): $PROXY_ALB_DNS"
```

### Step 4: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name private-ca-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 5: Create AgentCore Gateway Target — from `vpcegress/`

The target URL uses your public domain. The `routingDomain` routes traffic to the proxy ALB.

> [!NOTE]
> The `create_target.py` script does not yet support `routingDomain`. Use the [notebook](./02-private-certificate-authority.ipynb) for the full boto3 target creation with `routingDomain`, or run directly:

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 -c "
import boto3, json, os
control = boto3.client('bedrock-agentcore-control')

with open('../vpcegress/openapi-schemas/openapi-private.json') as f:
    schema = json.load(f)
schema['servers'] = [{'url': f\"https://${DOMAIN}\"}]

response = control.create_gateway_target(
    gatewayIdentifier=os.environ['GATEWAY_ID'],
    name='private-ca-proxy',
    targetConfiguration={'mcp': {'openApiSchema': {'inlinePayload': json.dumps(schema)}}},
    credentialProviderConfigurations=[{
        'credentialProviderType': 'API_KEY',
        'credentialProvider': {'apiKeyCredentialProvider': {
            'providerArn': '$(python3 -c \"import boto3; a=boto3.client(\\\"sts\\\").get_caller_identity()[\\\"Account\\\"]; r=boto3.Session().region_name; print(f\\\"arn:aws:bedrock-agentcore:{r}:{a}:token-vault/default/apikeycredentialprovider/private-ca-api-key\\\")\")',
            'credentialParameterName': 'x-api-key',
            'credentialLocation': 'HEADER',
        }}
    }],
    privateEndpoint={
        'managedVpcResource': {
            'vpcIdentifier': os.environ['VPC_USW2_ID'],
            'subnetIds': os.environ['VPC_USW2_PRIVATE_SUBNETS'].split(','),
            'endpointIpAddressType': 'IPV4',
            'securityGroupIds': [os.environ['PROXY_ALB_SG_ID']],
            'routingDomain': os.environ['PROXY_ALB_DNS'],
        }
    },
)
print(f\"Target ID: {response['targetId']}\")
print(f\"Status: {response['status']}\")
"
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 6: Invoke the API through AgentCore Gateway

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

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | python3 -m json.tool
```

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"private-ca-proxy___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name private-ca-proxy
python3 scripts/delete_credential.py --name private-ca-api-key --type api-key
```

Destroy CDK stacks (from project root):

```bash
cdk destroy PublicCertProxy \
  -c "publicCertArn=$CERT_ARN" -c "publicDomain=$DOMAIN" \
  -c "backendIp=$BACKEND_IP" -c "backendDomain=$CERT_DOMAIN" --force

cdk destroy PrivateCaBackend --force
cdk destroy ShortLivedPrivateCa --force
```

> [!WARNING]
> The `ShortLivedPrivateCa` stack includes an AWS Private CA ($50/month). Delete it to avoid charges.

## Documentation

- [Private Endpoints with VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-egress-private-endpoints.html)
- [AWS Private CA](https://docs.aws.amazon.com/privateca/latest/userguide/PcaWelcome.html)
- [ALB Host Header Conditions](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-listeners.html)
