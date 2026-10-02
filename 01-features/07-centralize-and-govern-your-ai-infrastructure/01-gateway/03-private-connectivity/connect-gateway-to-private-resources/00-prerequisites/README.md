# Prerequisites

This lab deploys the foundational infrastructure for all VPC egress tutorials: [bootstraps CDK](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html), deploys VPCs across regions, and sets up the shared AgentCore gateway via the [vpcegress/](../vpcegress/) AgentCore CLI project.

![Multi-account architecture](./images/multi-account.png)

## Domain and Certificate Guides

AgentCore gateway VPC egress requires a **publicly trusted TLS certificate** on the target endpoint. These guides explain each DNS/certificate combination:

| Guide | Description |
|-------|-------------|
| [Create an ACM Public Certificate](./create-acm-public-certificate.md) | Request, validate via DNS, and verify an ACM public certificate |
| [Create a Public DNS Record](./create-public-dns-record.md) | Create a CNAME in a public hosted zone pointing to your internal load balancer |
| [Create a Private Hosted Zone](./create-private-hosted-zone.md) | Create a Route 53 private hosted zone with an Alias record |

## VPC Architecture

Each VPC is created with three subnet types:
- **Public subnet** — hosts the NAT gateway and Internet gateway for outbound internet access
- **Private subnet** (with NAT) — workloads run here; can reach the internet via NAT but are not directly accessible from outside
- **Isolated subnet** — no internet access at all; used for resources that should have no outbound connectivity

![mul-arch](./images/multi-account.png)

## Prerequisites

- **[Node.js](https://nodejs.org/en/download)** v18 or later
- **[AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)** v2
- **[Docker](https://docs.docker.com/engine/install/)**
- **[AWS CDK CLI](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html)**
- **[TypeScript](https://www.typescriptlang.org/download/)**
- **[AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore)**: `npm install -g @aws/agentcore@0.30.0`
- Python 3.12+

## Deployment Steps

> [!IMPORTANT]
> Unless noted otherwise, all commands run from the **project root** (where `cdk.json` lives):
> `connect-gateway-to-private-resources/`

### Step 1: Verify prerequisites

```bash
node --version
npm --version
aws --version
docker --version
cdk --version
agentcore --version
```

### Step 2: Install project dependencies

```bash
npm install
```

### Step 3: Configure AWS credentials

Update the profile name to match your AWS CLI profile:

```bash
export ACCOUNT_A_PROFILE="default"

export ACCOUNT_A_ID=$(aws sts get-caller-identity --profile $ACCOUNT_A_PROFILE --query Account --output text)
echo "Account A: $ACCOUNT_A_ID (profile: $ACCOUNT_A_PROFILE)"
```

### Step 4: Bootstrap CDK

CDK bootstrap provisions the resources CDK needs to deploy (S3 bucket for assets, ECR repo for container images, IAM roles). This only needs to be done once per account/region.

Bootstrap **us-west-2** (required for all labs):

```bash
cdk bootstrap aws://$ACCOUNT_A_ID/us-west-2 --profile $ACCOUNT_A_PROFILE
```

Bootstrap **us-east-1** (required only for the [VPC Peering lab](../01-managed-vpc-resource/02-peering.md)):

```bash
cdk bootstrap aws://$ACCOUNT_A_ID/us-east-1 --profile $ACCOUNT_A_PROFILE
```

### Step 5: Deploy VPCs

All VPCs have VPC Flow Logs enabled, sending traffic logs to CloudWatch (1-month retention).

| Stack | Region | Description | Required for |
|-------|--------|-------------|-------------|
| VpcegressStack-USWest2 | us-west-2 | VPC (10.0.0.0/16) | All labs |
| VpcegressStack-USEast1 | us-east-1 | VPC (10.1.0.0/16) | [VPC Peering lab](../01-managed-vpc-resource/02-peering.md) |
| PeeringApigw-USEast1 | us-east-1 | Private API gateway + VPCE | [VPC Peering lab](../01-managed-vpc-resource/02-peering.md) |
| VpcPeeringStack | us-west-2 | VPC peering + routes | [VPC Peering lab](../01-managed-vpc-resource/02-peering.md) |

![acca](./images/account-a.png)

Deploy the primary VPC (us-west-2):

```bash
cdk deploy VpcegressStack-USWest2 \
  --profile $ACCOUNT_A_PROFILE \
  --require-approval never \
  --outputs-file vpc-outputs.json
```

Capture the VPC outputs:

```bash
export VPC_USW2_ID=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['VpcId'])")
export VPC_USW2_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('vpc-outputs.json'))['VpcegressStack-USWest2']['PrivateSubnetIds'])")

echo "VPC ID:          $VPC_USW2_ID"
echo "Private subnets: $VPC_USW2_PRIVATE_SUBNETS"
```

(Optional) Deploy us-east-1 stacks for the VPC Peering lab:

```bash
cdk deploy VpcegressStack-USEast1 PeeringApigw-USEast1 VpcPeeringStack \
  --profile $ACCOUNT_A_PROFILE \
  --require-approval never \
  --outputs-file peering-outputs.json
```

### Step 6: Deploy Amazon Cognito (shared)

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../00-optional-setup/) for full details.

If you haven't deployed the shared Cognito stack yet, deploy it using the Launch Stack button in [00-optional-setup](../../../../00-optional-setup/) or via the CLI:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

aws cloudformation deploy \
  --template-file vpcegress/cloudformation/cognito-signup-stack.yaml \
  --stack-name $COGNITO_STACK_NAME \
  --no-fail-on-empty-changeset
```

### Step 7: Deploy Amazon Bedrock AgentCore gateway

> [!NOTE]
> The gateway is managed via the AgentCore CLI project at [`vpcegress/`](../vpcegress/). All subsequent labs share this gateway.

Navigate to the [`vpcegress/`](../vpcegress/) directory and run:

```bash
cd vpcegress

export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

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

agentcore add gateway \
  --name vpc-egress-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'vpc-egress-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 8 (Optional): Deploy VPC in a second account

If you have a second AWS account for cross-account testing, follow these steps. This step is **required** to run the [Cross-Account lab](../02-self-managed-lattice/02-cross-account.md).

| Stack | Account | Region | CIDR |
|-------|---------|--------|------|
| VpcegressStack-USWest2-AccountB | Account B | us-west-2 | 10.2.0.0/16 |

![mul](./images/multi-account.png)

> [!IMPORTANT]
> Using long-term access keys is **not an AWS best practice**. AWS recommends using [IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/what-is.html) with temporary credentials via `aws sso login`. We use access keys here only to quickly get started.

```bash
export ACCOUNT_B_PROFILE="account-b"

# Configure credentials for Account B
aws configure --profile $ACCOUNT_B_PROFILE

export ACCOUNT_B_ID=$(aws sts get-caller-identity --profile $ACCOUNT_B_PROFILE --query Account --output text)
echo "Account B: $ACCOUNT_B_ID"
```

Bootstrap and deploy:

```bash
cdk bootstrap aws://$ACCOUNT_B_ID/us-west-2 --profile $ACCOUNT_B_PROFILE

ACCOUNT_B_ID=$ACCOUNT_B_ID cdk deploy VpcegressStack-USWest2-AccountB \
  --profile $ACCOUNT_B_PROFILE \
  --require-approval never \
  --outputs-file vpc-outputs-account-b.json
```

## Cleanup

> [!WARNING]
> The AgentCore gateway cannot be deleted while it has targets. Delete all targets from subsequent labs first.

> [!NOTE]
> The VPC stack may fail to delete if security groups or ENIs from other labs are still present. Ensure all lab targets are deleted and manually remove any retained security groups and orphaned ENIs before destroying VPC stacks.

From the [`vpcegress/`](../vpcegress/) directory, remove the gateway:

```bash
agentcore remove gateway --name vpc-egress-gateway -y
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

Destroy the CDK infrastructure stacks (from the project root):

```bash
cdk destroy VpcegressStack-USWest2 --profile $ACCOUNT_A_PROFILE --force
```

If you deployed peering/cross-account stacks:

```bash
cdk destroy VpcPeeringStack PeeringApigw-USEast1 VpcegressStack-USEast1 \
  --profile $ACCOUNT_A_PROFILE --force

# Account B (if deployed)
# cdk destroy VpcegressStack-USWest2-AccountB --profile $ACCOUNT_B_PROFILE --force
```

## Documentation

- [AgentCore gateway VPC Egress](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress.html)
- [AWS CDK Bootstrapping](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html)
- [VPC Peering](https://docs.aws.amazon.com/vpc/latest/peering/what-is-vpc-peering.html)
