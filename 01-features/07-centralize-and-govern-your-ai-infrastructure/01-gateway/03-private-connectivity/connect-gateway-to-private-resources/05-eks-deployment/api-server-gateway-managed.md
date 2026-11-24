# Connecting EKS REST API to AgentCore Gateway

Deploy a REST API (FastAPI) on Amazon EKS inside a private VPC, then connect it to [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) as an **MCP target with an OpenAPI schema**, using managed VPC egress with an internal NLB.

Unlike the MCP server lab, this lab uses an **OpenAPI schema** to describe the API endpoints. AgentCore Gateway uses the schema to expose the API operations as tools that AI agents can invoke.

![arch](./images/eks-api.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/README.md) (VPC + AgentCore Gateway deployed)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- A `kubectl` context (`CLUSTER_CONTEXT`) for this **private** EKS cluster, reachable from your authorized VPC-connected workstation. Review the optional EKS IAM/cleanup limits in [the migration notes](../vpcegress/README.md).
- An [ACM public certificate](../00-prerequisites/concepts/create-acm-public-certificate.md) for TLS termination on the NLB

> [!IMPORTANT]
> If you previously ran the MCP server lab, clean up that stack first (delete the gateway targets and `cdk destroy McpEks`) before deploying this one. The Shared EKS Cluster does not need to be destroyed.

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root**. Script commands run from [`vpcegress/`](../vpcegress/).

### Step 1: Configure domain and certificate — from project root

```bash
export CERT_ARN="arn:aws:acm:us-west-2:123456789012:certificate/your-cert-id"
export DOMAIN="api.eks.yourcompany.com"
export ACCOUNT_A_ID=$(aws sts get-caller-identity --query Account --output text)
```

### Step 2: Deploy REST API on EKS (CDK) — from project root

Deploy the shared EKS cluster (if not already deployed):

```bash
ACCOUNT_A_ID=$ACCOUNT_A_ID cdk deploy SharedEksCluster --require-approval never --outputs-file eks-cluster-outputs.json
```

Deploy the REST API with NGINX Ingress + NLB:

```bash
ACCOUNT_A_ID=$ACCOUNT_A_ID cdk deploy ApiEks \
  -c "publicCertArn=$CERT_ARN" \
  -c "privateDomain=$DOMAIN" \
  --require-approval never \
  --outputs-file eks-api-outputs.json
```

The NLB is created asynchronously by Kubernetes. Capture actual CDK zone outputs
and select the NLB by the Service's exact DNS name:

```bash
export PRIVATE_ZONE_ID=$(python3 -c "import json; print(json.load(open('eks-api-outputs.json'))['ApiEks']['PrivateZoneId'])")
export DOMAIN=$(python3 -c "import json; print(json.load(open('eks-api-outputs.json'))['ApiEks']['PrivateDomain'])")
kubectl --context "$CLUSTER_CONTEXT" -n rest-api wait service/rest-api-nlb --for=jsonpath='{.status.loadBalancer.ingress}' --timeout=5m
export NLB_DNS=$(kubectl --context "$CLUSTER_CONTEXT" -n rest-api get service rest-api-nlb -o 'jsonpath={.status.loadBalancer.ingress[0].hostname}')
export NLB_ARN=$(aws elbv2 describe-load-balancers --query "LoadBalancers[?DNSName=='$NLB_DNS'].LoadBalancerArn | [0]" --output text)
python3 vpcegress/scripts/configure_eks_endpoint.py --name eks-api \
  --nlb-arn "$NLB_ARN" --zone-id "$PRIVATE_ZONE_ID" --domain "$DOMAIN" --vpc-id "$VPC_USW2_ID"
export RESOURCE_GATEWAY_SG_ID=$(python3 -c "import json; print(json.load(open('vpcegress/.vpc-state/eks-endpoint-eks-api.json'))['securityGroupId'])")
export API_ENDPOINT="https://$DOMAIN"
# This original demo backend does not enforce API keys. The target API requires
# a provider configuration; use a non-secret demo value, never a production key.
export API_KEY_VALUE="dummy-key-for-eks-api"
```

For multiple NLB security groups, inspect them before running the helper and add
`--security-group-id <owned-frontend-sg>`; do not modify a shared backend group.
For a legacy NLB without its own SG, the helper owns a dedicated Resource
Gateway group: no ingress, TCP 443 egress to this VPC CIDR only. Its ID is passed
to the target; the restricted default SG is never selected or opened. The
endpoint cleanup removes the dedicated group after target/ENI release.

### Step 3: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name eks-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 4: Create AgentCore Gateway Target (OpenAPI schema) — from `vpcegress/`

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('../vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

python3 scripts/configure_gateway_permissions.py --credential eks-api-key
agentcore deploy --yes --skip-bootstrap

python3 scripts/create_target.py \
  --name eks-api-server \
  --openapi-schema-file openapi-schemas/openapi-eks-api.json \
  --endpoint $API_ENDPOINT \
  --vpc-id $VPC_USW2_ID \
  --subnet-ids $VPC_USW2_PRIVATE_SUBNETS \
  --security-group-ids "$RESOURCE_GATEWAY_SG_ID" \
  --credential-provider-arn "$(python3 -c "import json; print(json.load(open('.vpc-state/credential-eks-api-key.json'))['arn'])")"
```

> [!NOTE]
> The helper sends `openApiSchema.inlinePayload` using the checked-in schema,
> with the endpoint URL supplied above. The simple EKS API is a network demo,
> not an API-key-enforcing production backend.

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 5: Invoke the API through AgentCore Gateway

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

List tools (API operations exposed as MCP tools):

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
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"eks-api-server___healthCheck","arguments":{}},"id":2}' | python3 -m json.tool
```

List items:

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"eks-api-server___listItems","arguments":{}},"id":3}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name eks-api-server &&
python3 scripts/delete_credential.py --name eks-api-key --type api-key
```

Destroy CDK stacks only after deleting the SDK alias and Kubernetes-owned NLB
(from project root, with the same verified cluster context). Retained Kubernetes
manifests and their NLB must be cleaned up before the shared cluster.

```bash
python3 vpcegress/scripts/configure_eks_endpoint.py --name eks-api --delete
kubectl --context "$CLUSTER_CONTEXT" -n rest-api delete service rest-api-nlb --wait=true --timeout=5m
aws elbv2 wait load-balancers-deleted --load-balancer-arns "$NLB_ARN"
kubectl --context "$CLUSTER_CONTEXT" delete namespace rest-api --wait=true --timeout=5m

ACCOUNT_A_ID=$ACCOUNT_A_ID cdk destroy ApiEks \
  -c "publicCertArn=$CERT_ARN" -c "privateDomain=$DOMAIN" --force

# Only destroy shared EKS cluster if no other EKS labs are using it:
# ACCOUNT_A_ID=$ACCOUNT_A_ID cdk destroy SharedEksCluster --force
```

## Documentation

- [Amazon EKS](https://docs.aws.amazon.com/eks/latest/userguide/what-is-eks.html)
- [OpenAPI Targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-openapi.html)
- [AgentCore Gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
