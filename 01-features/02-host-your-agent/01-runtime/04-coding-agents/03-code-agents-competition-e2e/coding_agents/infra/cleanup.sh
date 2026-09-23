#!/usr/bin/env bash
# Tear down shared infrastructure (VPC + S3 Files). Keeps the S3 bucket.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${SCRIPT_DIR}/../infra.config"

if [ ! -f "$CONFIG" ]; then
  echo "No infra.config found. Nothing to clean up."
  exit 0
fi

source "$CONFIG"

echo "Deleting CloudFormation stack: ${INFRA_STACK_NAME}..."
aws cloudformation delete-stack --stack-name "${INFRA_STACK_NAME}" --region "${INFRA_REGION}"

echo "Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete \
  --stack-name "${INFRA_STACK_NAME}" \
  --region "${INFRA_REGION}"

echo "Stack deleted."
echo "S3 bucket kept: s3://${INFRA_BUCKET}"

rm -f "$CONFIG"
echo "Done."
