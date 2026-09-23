#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:?Usage: $0 <enable|disable> <region>}"
REGION="${2:?Usage: $0 <enable|disable> <region>}"
STACK_NAME="agentcore-replication-regional"

echo ">>> Looking up memory ID in ${REGION}..."
MEMORY_ID=$(aws bedrock-agentcore-control list-memories --region "${REGION}" \
  --query "memories[?contains(id,'replication_memory') && status=='ACTIVE'].id" \
  --output text)

if [ -z "${MEMORY_ID}" ]; then
  echo "❌ No active replication memory found in ${REGION}"
  exit 1
fi
echo "    Memory: ${MEMORY_ID}"

if [ "${ACTION}" = "enable" ]; then
  CONFIG=$(aws cloudformation describe-stacks --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='StreamDeliveryConfig'].OutputValue" \
    --output text)

  if [ -z "${CONFIG}" ] || [ "${CONFIG}" = "None" ]; then
    echo "❌ StreamDeliveryConfig output not found"
    exit 1
  fi

  echo ">>> Enabling streaming in ${REGION}..."
  aws bedrock-agentcore-control update-memory \
    --region "${REGION}" \
    --memory-id "${MEMORY_ID}" \
    --stream-delivery-resources "${CONFIG}"

elif [ "${ACTION}" = "disable" ]; then
  echo ">>> Disabling streaming in ${REGION}..."
  aws bedrock-agentcore-control update-memory \
    --region "${REGION}" \
    --memory-id "${MEMORY_ID}" \
    --stream-delivery-resources '{"resources":[]}'

else
  echo "❌ Unknown action: ${ACTION} (use 'enable' or 'disable')"
  exit 1
fi

echo "✅ Streaming ${ACTION}d in ${REGION} for ${MEMORY_ID}"
