#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PRIMARY_REGION="${1:-us-east-1}"
SECONDARY_REGION="${2:-us-west-2}"
ACCOUNT_ID="${3:-$(aws sts get-caller-identity --query Account --output text)}"
STACK_PREFIX="agentcore-replication"
STREAM_NAME="agentcore-memory-stream"
S3_BUCKET="${STACK_PREFIX}-artifacts-${ACCOUNT_ID}"

echo "=== AgentCore Memory Cross-Region Replication Deployment ==="
echo "Primary:   ${PRIMARY_REGION}"
echo "Secondary: ${SECONDARY_REGION}"
echo "Account:   ${ACCOUNT_ID}"

# --- Step 1: Package Lambda ---
echo ">>> Packaging Lambda..."
PACKAGE_DIR=$(mktemp -d)
ZIP_PATH="${SCRIPT_DIR}/handler.zip"

pip install boto3 -t "${PACKAGE_DIR}" --quiet
cp "${SCRIPT_DIR}/handler.py" "${PACKAGE_DIR}/"
(cd "${PACKAGE_DIR}" && zip -r9 "${ZIP_PATH}" . --quiet)
rm -rf "${PACKAGE_DIR}"

for REGION in "${PRIMARY_REGION}" "${SECONDARY_REGION}"; do
  aws s3 mb "s3://${S3_BUCKET}-${REGION}" --region "${REGION}" 2>/dev/null || true
  aws s3 cp "${ZIP_PATH}" "s3://${S3_BUCKET}-${REGION}/lambda/handler.zip" --region "${REGION}"
done
rm -f "${ZIP_PATH}"

# --- Step 2: Deploy Global Stack ---
echo ">>> Deploying global stack in ${PRIMARY_REGION}..."
aws cloudformation deploy \
  --region "${PRIMARY_REGION}" \
  --stack-name "${STACK_PREFIX}-global" \
  --template-file "${SCRIPT_DIR}/global-stack.yaml" \
  --parameter-overrides \
    PrimaryRegion="${PRIMARY_REGION}" \
    SecondaryRegion="${SECONDARY_REGION}" \
  --no-fail-on-empty-changeset

echo ">>> Waiting for DynamoDB Global Table replication..."
sleep 30

# --- Step 3: Deploy Regional Stacks ---
for REGION in "${PRIMARY_REGION}" "${SECONDARY_REGION}"; do
  if [ "${REGION}" = "${PRIMARY_REGION}" ]; then
    ENV="primary"; REMOTE="${SECONDARY_REGION}"
  else
    ENV="secondary"; REMOTE="${PRIMARY_REGION}"
  fi

  echo ">>> Deploying regional stack in ${REGION} (${ENV})..."
  aws cloudformation deploy \
    --region "${REGION}" \
    --stack-name "${STACK_PREFIX}-regional" \
    --template-file "${SCRIPT_DIR}/regional-stack.yaml" \
    --parameter-overrides \
      Environment="${ENV}" \
      RemoteRegion="${REMOTE}" \
      RemoteMemoryId="PLACEHOLDER" \
      KinesisStreamName="${STREAM_NAME}" \
      LambdaS3Bucket="${S3_BUCKET}-${REGION}" \
      LambdaS3Key="lambda/handler.zip" \
    --capabilities CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset
done

# --- Step 4: Create AgentCore Memory ---
for REGION in "${PRIMARY_REGION}" "${SECONDARY_REGION}"; do
  STREAM_ARN="arn:aws:kinesis:${REGION}:${ACCOUNT_ID}:stream/${STREAM_NAME}"

  if [ "${REGION}" = "${PRIMARY_REGION}" ]; then
    ENV="primary"
  else
    ENV="secondary"
  fi

  STREAMING_ROLE_ARN=$(aws cloudformation describe-stacks \
    --region "${REGION}" \
    --stack-name "${STACK_PREFIX}-regional" \
    --query "Stacks[0].Outputs[?OutputKey=='MemoryStreamingRoleArn'].OutputValue" \
    --output text)

  echo ">>> Creating AgentCore Memory in ${REGION}..."
  if [ "${ENV}" = "primary" ]; then
    STREAM_CONFIG="{\"resources\":[{\"kinesis\":{\"dataStreamArn\":\"${STREAM_ARN}\",\"contentConfigurations\":[{\"type\":\"MEMORY_RECORDS\",\"level\":\"FULL_CONTENT\"}]}}]}"
    MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
      --region "${REGION}" \
      --name "replication_memory_${ENV}" \
      --description "AgentCore Memory with cross-region replication streaming" \
      --event-expiry-duration 30 \
      --memory-execution-role-arn "${STREAMING_ROLE_ARN}" \
      --stream-delivery-resources "${STREAM_CONFIG}" \
      --query 'memory.id' --output text)
  else
    # Secondary: create WITHOUT streaming (will be enabled during failover)
    MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
      --region "${REGION}" \
      --name "replication_memory_${ENV}" \
      --description "AgentCore Memory with cross-region replication streaming" \
      --event-expiry-duration 30 \
      --memory-execution-role-arn "${STREAMING_ROLE_ARN}" \
      --query 'memory.id' --output text)
  fi

  echo "    Memory ID (${REGION}): ${MEMORY_ID}"
  eval "MEMORY_ID_${REGION//-/_}=${MEMORY_ID}"
done

# --- Step 5: Update Regional Stacks with Remote Memory IDs ---
PRIMARY_MEMORY_ID=$(eval echo "\$MEMORY_ID_${PRIMARY_REGION//-/_}")
SECONDARY_MEMORY_ID=$(eval echo "\$MEMORY_ID_${SECONDARY_REGION//-/_}")

echo ">>> Updating primary stack with secondary memory ID..."
aws cloudformation deploy \
  --region "${PRIMARY_REGION}" \
  --stack-name "${STACK_PREFIX}-regional" \
  --template-file "${SCRIPT_DIR}/regional-stack.yaml" \
  --parameter-overrides \
    Environment="primary" \
    RemoteRegion="${SECONDARY_REGION}" \
    RemoteMemoryId="${SECONDARY_MEMORY_ID}" \
    KinesisStreamName="${STREAM_NAME}" \
    LambdaS3Bucket="${S3_BUCKET}-${PRIMARY_REGION}" \
    LambdaS3Key="lambda/handler.zip" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

echo ">>> Updating secondary stack with primary memory ID..."
aws cloudformation deploy \
  --region "${SECONDARY_REGION}" \
  --stack-name "${STACK_PREFIX}-regional" \
  --template-file "${SCRIPT_DIR}/regional-stack.yaml" \
  --parameter-overrides \
    Environment="secondary" \
    RemoteRegion="${PRIMARY_REGION}" \
    RemoteMemoryId="${PRIMARY_MEMORY_ID}" \
    KinesisStreamName="${STREAM_NAME}" \
    LambdaS3Bucket="${S3_BUCKET}-${SECONDARY_REGION}" \
    LambdaS3Key="lambda/handler.zip" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

# --- Step 6: Seed DynamoDB ---
echo ">>> Seeding config table..."
DEPLOY_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for ITEM_JSON in \
  "{\"PK\":{\"S\":\"ACTIVE_REGION\"},\"region\":{\"S\":\"${PRIMARY_REGION}\"},\"updated_at\":{\"S\":\"${DEPLOY_TS}\"},\"updated_by\":{\"S\":\"deploy-script\"}}" \
  "{\"PK\":{\"S\":\"MEMORY_ID_PRIMARY\"},\"memory_id\":{\"S\":\"${PRIMARY_MEMORY_ID}\"},\"region\":{\"S\":\"${PRIMARY_REGION}\"}}" \
  "{\"PK\":{\"S\":\"MEMORY_ID_SECONDARY\"},\"memory_id\":{\"S\":\"${SECONDARY_MEMORY_ID}\"},\"region\":{\"S\":\"${SECONDARY_REGION}\"}}"
do
  aws dynamodb put-item \
    --region "${PRIMARY_REGION}" \
    --table-name AgentCoreMemoryReplicationConfig \
    --item "${ITEM_JSON}"
done

echo ""
echo "=== Deployment Complete ==="
echo "Primary Memory ID:   ${PRIMARY_MEMORY_ID}"
echo "Secondary Memory ID: ${SECONDARY_MEMORY_ID}"
echo "Active Region:       ${PRIMARY_REGION}"
