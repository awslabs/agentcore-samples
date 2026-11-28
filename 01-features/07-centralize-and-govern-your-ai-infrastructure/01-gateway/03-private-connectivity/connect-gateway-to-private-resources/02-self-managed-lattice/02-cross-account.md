# Cross-Account: Share a Private API via AWS RAM

Connect Amazon Bedrock AgentCore Gateway in **Account A** to a **Private API Gateway in Account B** using self-managed VPC Lattice and [AWS Resource Access Manager (RAM)](https://docs.aws.amazon.com/ram/latest/userguide/what-is.html).

This is the standard enterprise pattern for cross-account connectivity: the resource owner (Account B) creates the VPC Lattice resources and shares them with the gateway owner (Account A) via RAM.

## Architecture

![ram](./images/ram-target.png)

### How it works

1. **Account B** (resource owner): Private API Gateway + VPCE + VPC Lattice Resource Gateway + Resource Configuration
2. **Account B** shares the Resource Configuration with **Account A** via AWS RAM
3. **Account A** accepts the RAM share and sees the shared Resource Configuration ARN
4. **Account A** creates a gateway target with `selfManagedLatticeResource` pointing to the shared Resource Configuration ARN
5. AgentCore associates the Resource Configuration with its service network, completing cross-account connectivity

![ram-share](./images/ram.png)

## Prerequisites

- Completed [Lab 0](../00-prerequisites/README.md) (VPC us-west-2 + AgentCore Gateway deployed)
- **Account B configured**: In Lab 0, complete Step 8 (Account B credentials, bootstrap, VPC deployment)
- `GATEWAY_ID` and `GATEWAY_URL` exported (from the [vpcegress setup](../vpcegress/README.md))
- `ACCOUNT_B_PROFILE` and `ACCOUNT_B_ID` exported

## Deployment Steps

> [!IMPORTANT]
> CDK commands run from the **project root**. Script commands run from [`vpcegress/`](../vpcegress/). Commands specify which account profile to use.

### Step 1: Deploy Private API Gateway in Account B — from project root

```bash
cdk deploy CrossAccountApigw-AccountB \
  --profile $ACCOUNT_B_PROFILE \
  --require-approval never \
  --outputs-file cross-account-outputs.json
```

Capture outputs:

```bash
export CROSS_API_ID=$(python3 -c "import json; print(json.load(open('cross-account-outputs.json'))['CrossAccountApigw-AccountB']['ApiId'])")
export CROSS_VPCE_ID=$(python3 -c "import json; print(json.load(open('cross-account-outputs.json'))['CrossAccountApigw-AccountB']['VpceId'])")
export CROSS_VPCE_SG_ID=$(python3 -c "import json; print(json.load(open('cross-account-outputs.json'))['CrossAccountApigw-AccountB']['VpceSgId'])")
export CROSS_API_KEY_ID=$(python3 -c "import json; print(json.load(open('cross-account-outputs.json'))['CrossAccountApigw-AccountB']['ApiKeyId'])")

export CROSS_API_VPCE_DNS="${CROSS_API_ID}-${CROSS_VPCE_ID}.execute-api.us-west-2.amazonaws.com"
export API_KEY_VALUE=$(aws apigateway get-api-key --api-key $CROSS_API_KEY_ID --include-value --profile $ACCOUNT_B_PROFILE --query 'value' --output text)

echo "API-VPCE DNS: $CROSS_API_VPCE_DNS"
```

### Step 2: Create VPC Lattice resources in Account B — from project root

Create Resource Gateway and Resource Configuration in Account B's VPC:

```bash
export VPC_ACCB_ID=$(python3 -c "import json; print(json.load(open('vpc-outputs-account-b.json'))['VpcegressStack-USWest2-AccountB']['VpcId'])")
export VPC_ACCB_PRIVATE_SUBNETS=$(python3 -c "import json; print(json.load(open('vpc-outputs-account-b.json'))['VpcegressStack-USWest2-AccountB']['PrivateSubnetIds'])")

AWS_PROFILE="$ACCOUNT_B_PROFILE" python3 vpcegress/scripts/lattice_resources.py \
  --name cross-account \
  --vpc-id "$VPC_ACCB_ID" \
  --subnet-ids "$VPC_ACCB_PRIVATE_SUBNETS" \
  --security-group-ids "$CROSS_VPCE_SG_ID" \
  --dns-name "$CROSS_API_VPCE_DNS"

export RC_ARN=$(python3 -c "import json; print(json.load(open('vpcegress/.vpc-state/lattice-cross-account.json'))['resourceConfigurationArn'])")
```

### Step 3: Share Resource Configuration via AWS RAM — from project root

Account B shares the Resource Configuration with Account A:

![ram](./images/ram.png)

```bash
export ACCOUNT_A_ID=$(aws sts get-caller-identity --query Account --output text)

export SHARE_ARN=$(aws ram create-resource-share \
  --name "cross-account-lattice-share" \
  --resource-arns "$RC_ARN" \
  --principals "$ACCOUNT_A_ID" \
  --profile "$ACCOUNT_B_PROFILE" \
  --query 'resourceShare.resourceShareArn' --output text)
printf '%s\n' "$SHARE_ARN" > vpcegress/.vpc-state/cross-account-share-arn.txt
```

Account A accepts the RAM share:

```bash
export INVITATION_ARN=$(aws ram get-resource-share-invitations \
  --query "resourceShareInvitations[?resourceShareArn=='$SHARE_ARN' && status=='PENDING'].resourceShareInvitationArn | [0]" --output text)

aws ram accept-resource-share-invitation \
  --resource-share-invitation-arn $INVITATION_ARN

echo "RAM share accepted. RC ARN: $RC_ARN"
```

### Step 4: Create API Key Credential Provider — from `vpcegress/`

```bash
python3 scripts/create_credential.py \
  --name cross-account-apigw-api-key \
  --type api-key \
  --api-key-value $API_KEY_VALUE \
  --header-name x-api-key
```

### Step 5: Create AgentCore Gateway Target in Account A — from `vpcegress/`

Account A creates a target pointing to the shared Resource Configuration from Account B:

```bash
python3 scripts/configure_gateway_permissions.py --credential cross-account-apigw-api-key
agentcore deploy --yes --skip-bootstrap

python3 scripts/create_target.py \
  --name cross-account-apigw \
  --openapi-schema-file openapi-schemas/openapi-private-apigw.json \
  --endpoint "https://$CROSS_API_VPCE_DNS/prod" \
  --resource-configuration-identifier "$RC_ARN" \
  --credential-provider-arn "$(python3 -c "import json; print(json.load(open('.vpc-state/credential-cross-account-apigw-api-key.json'))['arn'])")"
```

## Demo

> [!TIP]
> Instead of running these commands, you can use the [AgentCore Gateway MCP Inspector](../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

### Step 6: Invoke the API through AgentCore Gateway

Follow the same token acquisition and invocation pattern as [lab 01 (Getting Started)](../01-managed-vpc-resource/01-getting-started.md#demo). The tool names are prefixed with `cross-account-apigw___`.

```bash
curl -s -X POST $GATEWAY_URL \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"cross-account-apigw___healthCheck","arguments":{}},"id":1}' | python3 -m json.tool
```

## Cleanup

From the [`vpcegress/`](../vpcegress/) directory (`cd vpcegress`):

```bash
python3 scripts/delete_target.py --name cross-account-apigw &&
python3 scripts/delete_credential.py --name cross-account-apigw-api-key --type api-key
```

Delete only the recorded RAM share, then VPC Lattice resources in Account B (from project root):

```bash
export SHARE_ARN=$(python3 -c "from pathlib import Path; print(Path('vpcegress/.vpc-state/cross-account-share-arn.txt').read_text().strip())")
aws ram delete-resource-share --resource-share-arn "$SHARE_ARN" --profile "$ACCOUNT_B_PROFILE" &&
AWS_PROFILE="$ACCOUNT_B_PROFILE" python3 vpcegress/scripts/lattice_resources.py --name cross-account --delete
```

The helper waits for association conflicts to clear with a bounded retry. On
failure stop here and keep the ledger; do not delete the VPC or assume cleanup.

Destroy the CDK stack:

```bash
cdk destroy CrossAccountApigw-AccountB --profile $ACCOUNT_B_PROFILE --force
```

## Documentation

- [AWS Resource Access Manager](https://docs.aws.amazon.com/ram/latest/userguide/what-is.html)
- [VPC Lattice Cross-Account Sharing](https://docs.aws.amazon.com/vpc-lattice/latest/ug/sharing.html)
- [Self-Managed VPC Lattice](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-vpc-egress-self-managed.html)
