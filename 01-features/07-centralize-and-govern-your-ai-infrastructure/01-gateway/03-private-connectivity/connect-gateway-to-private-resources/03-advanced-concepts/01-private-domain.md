# Private Domain with AgentCore Gateway (Private DNS)

Connect [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) to a resource that uses a **private domain** — a domain that only resolves inside your VPC via a [Route 53 private hosted zone](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zones-private.html).

With **Private DNS** enabled on your VPC (the default), AgentCore Gateway's managed Resource Gateway resolves the private domain via your VPC's DNS resolver.

## Architecture

![arch](./images/private-domain.png)

- The target URL uses your private FQDN (e.g., `https://internal.yourcompany.com`)
- A Route 53 **private hosted zone** for that FQDN, associated with your VPC, aliases the domain to an internal ALB
- The ALB terminates TLS with a publicly trusted ACM certificate for the same FQDN
- AgentCore Gateway's Resource Gateway resolves the domain via Private DNS → gets the ALB's private IPs → establishes TLS against the public cert → requests land on your backend

### How Private DNS makes this work

VPC Lattice's managed Resource Gateway uses your VPC's DNS resolver to look up the target endpoint domain. If your VPC is associated with a Route 53 private hosted zone for that domain, the resolver returns the record from that zone.

**Requirements:**
- **VPC DNS enabled** — `enableDnsSupport` and `enableDnsHostnames` are both `true` (the default)
- **Private hosted zone association** — the hosted zone must be associated with the VPC where the Resource Gateway ENIs live
- **Publicly trusted TLS certificate** — the ALB must present a cert issued by a public CA (ACM public certificate) covering the target FQDN

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- An [ACM public certificate](../00-prerequisites/create-acm-public-certificate.md) for a domain you own

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root** (where `cdk.json` lives). Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Configure domain and certificate — from project root

Set your ACM certificate ARN and the private FQDN it covers:

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="internal.yourcompany.com"

echo "Cert ARN: $CERT_ARN"
echo "Domain:   $DOMAIN"
```

### Step 2: Deploy infrastructure (CDK) — from project root

This stack deploys:
- An **EC2 instance** running a REST API (FastAPI) on HTTP port 8000
- An **internal ALB** with your public ACM certificate on HTTPS port 443, forwarding to the EC2 over HTTP
- A **Route 53 private hosted zone** for `$DOMAIN` associated with the VPC, with an apex Alias record pointing to the ALB

```bash
cdk deploy PrivateDomain \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --require-approval never \
  --outputs-file pd-outputs.json
```

Capture outputs:

```bash
export ALB_SG_ID=$(python3 -c "import json; print(json.load(open('pd-outputs.json'))['PrivateDomain']['AlbSgId'])")
export API_KEY_VALUE=$(python3 -c "import json; print(json.load(open('pd-outputs.json'))['PrivateDomain']['ApiKey'])")

echo "ALB SG:    $ALB_SG_ID"
echo "API Key:   ${API_KEY_VALUE:0:10}..."
```

### Step 3: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name private-domain-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 4: Create AgentCore Gateway Target — from `vpcegress/`

The target URL is `https://$DOMAIN` — resolves to the ALB's private IPs via the private hosted zone.

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name private-domain \
  --endpoint "https://$DOMAIN" \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids $ALB_SG_ID \
  --credential-provider-arn $(python3 -c "
import boto3
account = boto3.client('sts').get_caller_identity()['Account']
region = boto3.Session().region_name
print(f'arn:aws:bedrock-agentcore:{region}:{account}:token-vault/default/apikeycredentialprovider/private-domain-api-key')
")
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 5: Invoke the API through AgentCore Gateway

The target's private domain is resolved entirely via your VPC's DNS resolver — nothing is exposed publicly.

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

Health check:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"private-domain___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

Create an item:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"private-domain___createItem","arguments":{"name":"Widget","price":9.99}},"id":3}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name private-domain
python3 scripts/delete_credential.py --name private-domain-api-key --type api-key
```

Destroy the CDK stack (from project root):

```bash
cdk destroy PrivateDomain \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --force
```

> [!NOTE]
> The ALB security group may be retained during stack deletion because Resource Gateway ENIs still reference it. Wait a few minutes after target deletion, then:
> ```bash
> aws ec2 delete-security-group --group-id $ALB_SG_ID
> ```

## Documentation

- [Private DNS with VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress-managed.html)
- [Route 53 Private Hosted Zones](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zones-private.html)
- [ACM Public Certificates](https://docs.aws.amazon.com/acm/latest/userguide/gs-acm-request-public.html)
