# Connecting EKS MCP Servers to AgentCore Gateway

Deploy two [FastMCP](https://github.com/jlowin/fastmcp) servers on Amazon EKS inside a private VPC, fronted by an [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/) behind a single internal NLB, then connect them to [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) using managed VPC egress.

The MCP servers are reachable via a **private domain** in a Route 53 private hosted zone. NGINX does path-based routing so a single NLB serves both MCP servers (`/mcp-server/mcp` and `/stock-mcp/mcp`).

![arch](./images/eks-mcp.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/README.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- A `kubectl` context (`CLUSTER_CONTEXT`) for this **private** EKS cluster, reachable from your authorized VPC-connected workstation. Review the optional EKS IAM/cleanup limits in [the migration notes](../vpcegress/README.md).
- An [ACM public certificate](../00-prerequisites/concepts/create-acm-public-certificate.md) for TLS termination on the NLB

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root**. Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Configure domain and certificate — from project root

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="mcp.eks.yourcompany.com"
export ACCOUNT_A_ID=$(aws sts get-caller-identity --query Account --output text)
```

### Step 2: Deploy MCP servers on EKS (CDK) — from project root

Deploy the shared EKS cluster (first time only):

```bash
ACCOUNT_A_ID=$ACCOUNT_A_ID cdk deploy SharedEksCluster --require-approval never --outputs-file eks-cluster-outputs.json
```

Deploy the MCP servers with NGINX Ingress + NLB:

```bash
ACCOUNT_A_ID=$ACCOUNT_A_ID cdk deploy McpEks \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --require-approval never \
  --outputs-file eks-mcp-outputs.json
```

The NLB is created asynchronously by Kubernetes, not a CDK output. Read the
actual private-zone outputs, then select the NLB by the Service's exact DNS name:

```bash
export PRIVATE_ZONE_ID=$(python3 -c "import json; print(json.load(open('eks-mcp-outputs.json'))['McpEks']['PrivateZoneId'])")
export DOMAIN=$(python3 -c "import json; print(json.load(open('eks-mcp-outputs.json'))['McpEks']['PrivateDomain'])")
kubectl --context "$CLUSTER_CONTEXT" -n ingress-nginx wait service/nginx-ingress-nlb --for=jsonpath='{.status.loadBalancer.ingress}' --timeout=5m
export NLB_DNS=$(kubectl --context "$CLUSTER_CONTEXT" -n ingress-nginx get service nginx-ingress-nlb -o 'jsonpath={.status.loadBalancer.ingress[0].hostname}')
export NLB_ARN=$(aws elbv2 describe-load-balancers --query "LoadBalancers[?DNSName=='$NLB_DNS'].LoadBalancerArn | [0]" --output text)
python3 vpcegress/scripts/configure_eks_endpoint.py --name eks-mcp \
  --nlb-arn "$NLB_ARN" --zone-id "$PRIVATE_ZONE_ID" --domain "$DOMAIN" --vpc-id "$VPC_USW2_ID"
export RESOURCE_GATEWAY_SG_ID=$(python3 -c "import json; print(json.load(open('vpcegress/.vpc-state/eks-endpoint-eks-mcp.json'))['securityGroupId'])")
export MCP_ENDPOINT="https://$DOMAIN/mcp-server/mcp"
export STOCK_ENDPOINT="https://$DOMAIN/stock-mcp/mcp"
```

The helper **creates**, never overwrites, the alias record and records only new
ingress rules. For an NLB with multiple attached groups, inspect them first and
add `--security-group-id <owned-frontend-sg>` to the helper command; never select
a shared backend group arbitrarily. For a legacy NLB without its own SG, the
helper creates a separately tagged Resource Gateway group with **no ingress**
and **TCP 443 egress to the VPC CIDR only**. Its ID is always passed below; the
restricted default SG is never selected or modified. Endpoint cleanup deletes
that dedicated group only after targets are absent and ENIs release it.

### Step 3: Create AgentCore Gateway Targets — from `vpcegress/`

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/create_target.py \
  --name eks-mcp-server \
  --endpoint $MCP_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids "$RESOURCE_GATEWAY_SG_ID"
```

(Optional) Stock price MCP server:

```bash
python3 scripts/create_target.py \
  --name eks-stock-mcp \
  --endpoint $STOCK_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids "$RESOURCE_GATEWAY_SG_ID"
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to explore MCP tools interactively.

### Step 4: Invoke the MCP servers through AgentCore Gateway

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

Invoke `echo`:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"eks-mcp-server___echo","arguments":{"message":"Hello from EKS!"}},"id":2}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name eks-mcp-server
python3 scripts/delete_target.py --name eks-stock-mcp
```

Destroy CDK stacks only after deleting the SDK alias and Kubernetes-owned NLB
(from project root, with the same verified cluster context). The historical CDK
manifests are retained; deleting the cluster is **not** NLB cleanup.

```bash
python3 vpcegress/scripts/configure_eks_endpoint.py --name eks-mcp --delete
kubectl --context "$CLUSTER_CONTEXT" -n ingress-nginx delete service nginx-ingress-nlb --wait=true --timeout=5m
aws elbv2 wait load-balancers-deleted --load-balancer-arns "$NLB_ARN"
kubectl --context "$CLUSTER_CONTEXT" delete namespace mcp-server --wait=true --timeout=5m

ACCOUNT_A_ID=$ACCOUNT_A_ID cdk destroy McpEks \
  -c "publicCertArn=$CERT_ARN" -c "privateDomain=$DOMAIN" --force

# Only destroy shared EKS cluster if no other EKS labs are using it:
# ACCOUNT_A_ID=$ACCOUNT_A_ID cdk destroy SharedEksCluster --force
```

## Documentation

- [Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
