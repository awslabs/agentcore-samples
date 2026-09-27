#!/bin/bash
# Deploy Private Keycloak IdP + AgentCore Gateway sample
# Usage: ./deploy_sample.sh <DOMAIN> <HOSTED_ZONE_ID> <VPC_ID> <SUBNET_1> <SUBNET_2> <KC_PASSWORD>
set -e

DOMAIN=${1:?Usage: ./deploy_sample.sh DOMAIN HOSTED_ZONE_ID VPC_ID SUBNET_1 SUBNET_2 KC_PASSWORD}
HOSTED_ZONE_ID=${2:?}
VPC_ID=${3:?}
SUBNET_1=${4:?}
SUBNET_2=${5:?}
KC_PASSWORD=${6:?}
STACK_NAME="keycloak-private-idp-gw"
REGION=${AWS_DEFAULT_REGION:-us-east-1}
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

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

SG_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`SecurityGroupId`].OutputValue' --output text)
DISCOVERY_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)
INSTANCE_ID=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text)
PRIVATE_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text)

echo "Discovery URL: $DISCOVERY_URL"

echo ""
echo "=== Step 2: Configure Keycloak ==="
echo "Waiting for boot..."
sleep 120
python3 setup_keycloak.py --url "http://$PRIVATE_IP:8080" --password "$KC_PASSWORD"

echo ""
echo "=== Step 3: Create Lambda tool ==="
cd lambda && zip -j ban_appeal.zip ban_appeal.py && cd ..
aws lambda create-function \
  --function-name ban-appeal-tools \
  --runtime python3.12 \
  --handler ban_appeal.handler \
  --role "arn:aws:iam::$ACCOUNT_ID:role/AgentCoreRuntimeRole" \
  --zip-file fileb://lambda/ban_appeal.zip \
  --region "$REGION" --output text --query 'FunctionArn' 2>/dev/null || echo "Lambda exists"
aws lambda add-permission --function-name ban-appeal-tools --statement-id agentcore \
  --action lambda:InvokeFunction --principal bedrock-agentcore.amazonaws.com \
  --region "$REGION" 2>/dev/null || true
LAMBDA_ARN="arn:aws:lambda:$REGION:$ACCOUNT_ID:function:ban-appeal-tools"

echo ""
echo "=== Step 4: Create Gateway ==="
GW_RESULT=$(aws bedrock-agentcore-control create-gateway --cli-input-json "{
  \"name\": \"private-keycloak-gw\",
  \"roleArn\": \"arn:aws:iam::$ACCOUNT_ID:role/AgentCoreRuntimeRole\",
  \"protocolType\": \"MCP\",
  \"authorizerType\": \"CUSTOM_JWT\",
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
  }
}" --region "$REGION" --output json)

GW_ID=$(echo "$GW_RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin)['gatewayId'])")
GW_URL=$(echo "$GW_RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin)['gatewayUrl'])")
echo "Gateway ID: $GW_ID"
echo "Waiting for READY..."

for i in $(seq 1 20); do
  STATUS=$(aws bedrock-agentcore-control get-gateway --gateway-id "$GW_ID" --region "$REGION" --query 'status' --output text)
  [ "$STATUS" = "READY" ] && break
  sleep 30
done

echo ""
echo "=== Step 5: Register Lambda target ==="
aws bedrock-agentcore-control create-gateway-target \
  --gateway-identifier "$GW_ID" \
  --name ban-appeal-tools \
  --target-configuration "{\"mcp\":{\"lambda\":{\"lambdaArn\":\"$LAMBDA_ARN\",\"toolSchema\":{\"inlinePayload\":[{\"name\":\"check_enforcement_status\",\"description\":\"Check player ban status\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"player_id\":{\"type\":\"string\",\"description\":\"Player ID\"}},\"required\":[\"player_id\"]}},{\"name\":\"submit_appeal\",\"description\":\"Submit a ban appeal\",\"inputSchema\":{\"type\":\"object\",\"properties\":{\"player_id\":{\"type\":\"string\"},\"reason\":{\"type\":\"string\"}},\"required\":[\"player_id\",\"reason\"]}}]}}}}" \
  --credential-provider-configurations '[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]' \
  --region "$REGION" --output text --query 'targetId'

echo ""
echo "============================================"
echo "✅ Deployment complete!"
echo "   Gateway ID: $GW_ID"
echo "   Gateway URL: $GW_URL"
echo "   Gateway Status: $STATUS"
echo "   Discovery URL: $DISCOVERY_URL"
echo "============================================"
echo ""
echo "Test: python3 invoke.py --keycloak-url https://$DOMAIN --gateway-url $GW_URL"
