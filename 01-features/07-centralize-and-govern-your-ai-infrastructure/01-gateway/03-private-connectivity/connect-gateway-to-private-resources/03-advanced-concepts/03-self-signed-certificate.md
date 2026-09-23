# Self-Signed Certificate: ALB Proxy Solution

Connect [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) to an API that uses a **self-signed TLS certificate**. This lab uses the same ALB proxy pattern as the [Private CA lab](./02-private-certificate-authority.md) — the only difference is how the backend certificate was issued.

## The Problem

AgentCore Gateway validates TLS certificates against **public root CAs only**. Self-signed certificates are not trusted. The standard fix is to replace with a public ACM certificate, but if you don't own the domain, deploy an **internal ALB with a public certificate** in front.

![arch](./images/self-signed.png)

## Solution

Same as the [Private CA lab](./02-private-certificate-authority.md):

1. Deploy an internal ALB with your public ACM certificate
2. Use `routingDomain` to route through the ALB
3. ALB terminates TLS with the public cert, applies host header transform, forwards to backend over HTTPS

The ALB does not validate the backend's self-signed certificate when forwarding.

## Architecture

1. **Target URL**: `https://your-public-domain.com` (matches your public ACM cert)
2. **`routingDomain`**: proxy ALB DNS (publicly resolvable)
3. VPC Lattice routes to ALB → TLS handshake succeeds (public cert)
4. ALB terminates TLS → host header transform → forwards to backend over HTTPS (self-signed cert)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/00-vpc-gateway-setup.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- An [ACM public certificate](../00-prerequisites/create-acm-public-certificate.md) for a domain you own

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root**. Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Deploy a sample backend with self-signed cert — from project root

```bash
cdk deploy SelfSignedBackend --require-approval never --outputs-file selfsigned-outputs.json
```

```bash
export BACKEND_IP=$(python3 -c "import json; print(json.load(open('selfsigned-outputs.json'))['SelfSignedBackend']['Ec2PrivateIp'])")
export CERT_DOMAIN=$(python3 -c "import json; print(json.load(open('selfsigned-outputs.json'))['SelfSignedBackend']['CertDomain'])")
export API_KEY_VALUE=$(python3 -c "import json; print(json.load(open('selfsigned-outputs.json'))['SelfSignedBackend']['ApiKey'])")

echo "Backend IP:  $BACKEND_IP"
echo "Cert Domain: $CERT_DOMAIN"
```

### Step 2: Configure your public certificate

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="api.external.yourcompany.com"
```

### Step 3: Deploy the public cert proxy ALB — from project root

```bash
cdk deploy PublicCertProxy \
  -c "publicCertArn=$CERT_ARN" \
  -c "publicDomain=$DOMAIN" \
  -c "backendIp=$BACKEND_IP" \
  -c "backendDomain=$CERT_DOMAIN" \
  --require-approval never \
  --outputs-file proxy-outputs.json
```

```bash
export PROXY_ALB_DNS=$(python3 -c "import json; print(json.load(open('proxy-outputs.json'))['PublicCertProxy']['AlbDnsName'])")
export PROXY_ALB_SG_ID=$(python3 -c "import json; print(json.load(open('proxy-outputs.json'))['PublicCertProxy']['AlbSgId'])")

echo "Proxy ALB DNS (routingDomain): $PROXY_ALB_DNS"
```

### Step 4: Create credential + target — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name self-signed-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

> [!NOTE]
> The `create_target.py` script does not yet support `routingDomain`. See the [notebook](./03-self-signed-certificate.ipynb) for the full boto3 target creation, or refer to the [Private CA lab](./02-private-certificate-authority.md) which uses the same pattern.

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

Follow the same invocation pattern as [lab 01 (Private Domain)](./01-private-domain.md#demo).

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name self-signed-proxy
python3 scripts/delete_credential.py --name self-signed-api-key --type api-key
```

Destroy CDK stacks (from project root):

```bash
cdk destroy PublicCertProxy \
  -c "publicCertArn=$CERT_ARN" -c "publicDomain=$DOMAIN" \
  -c "backendIp=$BACKEND_IP" -c "backendDomain=$CERT_DOMAIN" --force

cdk destroy SelfSignedBackend --force
```

## Documentation

- [Private Endpoints with VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/vpc-egress-private-endpoints.html)
- [ALB HTTPS Listeners](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html)
