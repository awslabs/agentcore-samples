#!/bin/bash
# Cleanup Private Keycloak IdP + AgentCore Runtime sample
# Usage: ./cleanup_sample.sh <RUNTIME_ID>
set -e

RUNTIME_ID=${1:?Usage: ./cleanup_sample.sh RUNTIME_ID}
STACK_NAME="keycloak-private-idp"
REGION=${AWS_DEFAULT_REGION:-us-east-1}

echo "=== Deleting AgentCore Runtime ==="
aws bedrock-agentcore-control delete-agent-runtime \
  --agent-runtime-id "$RUNTIME_ID" \
  --region "$REGION" 2>/dev/null && echo "Runtime deletion initiated" || echo "Runtime not found"

echo ""
echo "=== Deleting CloudFormation stack ==="
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
echo "Stack deletion initiated. Waiting..."
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" 2>/dev/null || true

echo ""
echo "✅ Cleanup complete"
