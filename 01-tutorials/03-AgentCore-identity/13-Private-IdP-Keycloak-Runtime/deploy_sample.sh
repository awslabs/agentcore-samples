#!/bin/bash
# Deploy Private Keycloak IdP + AgentCore Runtime sample
# Usage: ./deploy_sample.sh <DOMAIN> <HOSTED_ZONE_ID> <VPC_ID> <SUBNET_1> <SUBNET_2> <KC_PASSWORD>
set -e

DOMAIN=${1:?Usage: ./deploy_sample.sh DOMAIN HOSTED_ZONE_ID VPC_ID SUBNET_1 SUBNET_2 KC_PASSWORD}
HOSTED_ZONE_ID=${2:?}
VPC_ID=${3:?}
SUBNET_1=${4:?}
SUBNET_2=${5:?}
KC_PASSWORD=${6:?}
STACK_NAME="keycloak-private-idp"
REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "=== Step 1: Deploy Keycloak infrastructure ==="
aws cloudformation deploy \
  --template-file keycloak-infra.yaml \
  --stack-name "$STACK_NAME" \
  --parameter-overrides \
    DomainName="$DOMAIN" \
    HostedZoneId="$HOSTED_ZONE_ID" \
    VpcId="$VPC_ID" \
    SubnetIds="$SUBNET_1,$SUBNET_2" \
    KeycloakAdminPassword="$KC_PASSWORD" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION"

echo ""
echo "=== Step 2: Get stack outputs ==="
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
SG_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`SecurityGroupId`].OutputValue' --output text)
DISCOVERY_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

echo "Instance: $INSTANCE_ID"
echo "Discovery URL: $DISCOVERY_URL"

echo ""
echo "=== Step 3: Configure Keycloak (via SSM) ==="
PRIVATE_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)

# Wait for Keycloak to boot then configure
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name "AWS-RunShellScript" \
  --parameters "{\"commands\":[\"sleep 90\",\"python3 -c \\\"import urllib.request,json; urllib.request.urlopen('http://127.0.0.1:8080/realms/master')\\\" && echo READY\"]}" \
  --region "$REGION" --query 'Command.CommandId' --output text)
echo "Waiting for Keycloak boot..."
aws ssm wait command-executed --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --region "$REGION" 2>/dev/null || sleep 120

python3 setup_keycloak.py --url "http://$PRIVATE_IP:8080" --password "$KC_PASSWORD"

echo ""
echo "=== Step 4: Create AgentCore Runtime ==="
RUNTIME_RESULT=$(aws bedrock-agentcore-control create-agent-runtime --cli-input-json "{
  \"agentRuntimeName\": \"private_keycloak_runtime\",
  \"agentRuntimeArtifact\": {\"containerConfiguration\": {\"containerUri\": \"$(aws sts get-caller-identity --query Account --output text).dkr.ecr.$REGION.amazonaws.com/agentcore-echo:latest\"}},
  \"roleArn\": \"arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/AgentCoreRuntimeRole\",
  \"networkConfiguration\": {\"networkMode\": \"PUBLIC\"},
  \"authorizerConfiguration\": {
    \"customJWTAuthorizer\": {
      \"discoveryUrl\": \"$DISCOVERY_URL\",
      \"allowedClients\": [\"content-export-adapter\"],
      \"allowedAudience\": [\"account\"],
      \"privateEndpoint\": {
        \"managedVpcResource\": {
          \"vpcIdentifier\": \"$VPC_ID\",
          \"subnetIds\": [\"$SUBNET_1\", \"$SUBNET_2\"],
          \"endpointIpAddressType\": \"IPV4\",
          \"securityGroupIds\": [\"$SG_ID\"]
        }
      }
    }
  },
  \"protocolConfiguration\": {\"serverProtocol\": \"HTTP\"}
}" --region "$REGION" --output json)

RUNTIME_ID=$(echo "$RUNTIME_RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin)['agentRuntimeId'])")
echo "Runtime ID: $RUNTIME_ID"
echo "Waiting for READY status..."

for i in $(seq 1 20); do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" --query 'status' --output text)
  [ "$STATUS" = "READY" ] && break
  sleep 30
done

echo ""
echo "============================================"
echo "✅ Deployment complete!"
echo "   Runtime ID: $RUNTIME_ID"
echo "   Runtime Status: $STATUS"
echo "   Discovery URL: $DISCOVERY_URL"
echo "   Keycloak Admin: https://$DOMAIN/admin"
echo "============================================"
