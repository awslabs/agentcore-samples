#!/bin/bash
# Cleanup Private Keycloak IdP + AgentCore Gateway sample
# Usage: ./cleanup_sample.sh <GATEWAY_ID>
set -e

GW_ID=${1:?Usage: ./cleanup_sample.sh GATEWAY_ID}
STACK_NAME="keycloak-private-idp-gw"
REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "=== Deleting Gateway targets ==="
for TARGET_ID in $(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query 'targets[*].targetId' --output text 2>/dev/null); do
  aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$TARGET_ID" --region "$REGION"
  echo "Deleted target: $TARGET_ID"
done

echo ""
echo "=== Deleting Gateway ==="
aws bedrock-agentcore-control delete-gateway --gateway-id "$GW_ID" --region "$REGION" 2>/dev/null && echo "Gateway deletion initiated" || echo "Gateway not found"

echo ""
echo "=== Deleting Lambda ==="
aws lambda delete-function --function-name ban-appeal-tools --region "$REGION" 2>/dev/null || true

echo ""
echo "=== Deleting CloudFormation stack ==="
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
echo "Stack deletion initiated. Waiting..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true

echo ""
echo "✅ Cleanup complete"
