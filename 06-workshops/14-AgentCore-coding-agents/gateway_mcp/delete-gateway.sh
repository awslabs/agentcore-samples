#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

echo "==> Deleting AgentCore Gateway: ${GATEWAY_NAME}"

GATEWAY_ID=$(state_get "gateway_id")

if [[ -z "$GATEWAY_ID" ]]; then
  echo "No gateway ID in state. Trying by name..."
  EXISTING=$(aws bedrock-agentcore-control get-gateway \
    --gateway-identifier "$GATEWAY_NAME" \
    --region "$AWS_REGION" 2>/dev/null || true)
  if [[ -z "$EXISTING" ]]; then
    echo "Gateway '${GATEWAY_NAME}' not found. Nothing to delete."
    exit 0
  fi
  GATEWAY_ID=$(echo "$EXISTING" | jq -r '.gatewayId')
fi

# Delete all targets first
echo "Listing gateway targets..."
TARGETS=$(aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier "$GATEWAY_ID" \
  --region "$AWS_REGION" \
  --query 'items[].targetId' --output text 2>/dev/null || true)

for TARGET_ID in $TARGETS; do
  echo "Deleting gateway target '${TARGET_ID}'..."
  aws bedrock-agentcore-control delete-gateway-target \
    --gateway-identifier "$GATEWAY_ID" \
    --target-id "$TARGET_ID" \
    --region "$AWS_REGION"
done

# Wait for targets to be fully deleted
if [[ -n "$TARGETS" ]]; then
  echo "Waiting for targets to be deleted..."
  sleep 5
fi

# Delete gateway
echo "Deleting gateway '${GATEWAY_ID}'..."
aws bedrock-agentcore-control delete-gateway \
  --gateway-identifier "$GATEWAY_ID" \
  --region "$AWS_REGION"

# Delete gateway IAM role
GW_ROLE_NAME=$(state_get "gateway_role_name")
if [[ -n "$GW_ROLE_NAME" ]] && aws iam get-role --role-name "$GW_ROLE_NAME" &>/dev/null; then
  echo "Deleting gateway IAM role '${GW_ROLE_NAME}'..."
  INLINE_POLICIES=$(aws iam list-role-policies --role-name "$GW_ROLE_NAME" --query 'PolicyNames[]' --output text)
  for policy_name in $INLINE_POLICIES; do
    aws iam delete-role-policy --role-name "$GW_ROLE_NAME" --policy-name "$policy_name"
  done
  aws iam delete-role --role-name "$GW_ROLE_NAME"
fi

state_set "gateway_id" ""
state_set "gateway_url" ""
state_set "gateway_target_name" ""
state_set "gateway_role_arn" ""
state_set "gateway_role_name" ""
echo "==> Gateway deleted."
