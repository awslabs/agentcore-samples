#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage: $(basename "$0") --stack-name <name> --prefix <prefix> --region <aws-region>

Tears down resources created by deploy.sh:
  1. Deletes the CloudFormation stack
  2. Empties and deletes the Lambda layer S3 bucket

Options:
  --stack-name   CloudFormation stack name (required)
  --prefix       Prefix used during deploy (required)
  --region       AWS region (required)
EOF
  exit 1
}

STACK_NAME=""
PREFIX=""
AWS_REGION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    --prefix)     PREFIX="$2";     shift 2 ;;
    --region)     AWS_REGION="$2"; shift 2 ;;
    -h|--help)    usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

[[ -z "${STACK_NAME}" || -z "${PREFIX}" || -z "${AWS_REGION}" ]] && usage

# --- Resolve S3 bucket name ---
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "${AWS_REGION}")"
S3_BUCKET="${PREFIX}-deploy-artifacts-${ACCOUNT_ID}-${AWS_REGION}"

# --- Delete CloudFormation stack ---
echo "Deleting CloudFormation stack: ${STACK_NAME}..."
aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${AWS_REGION}"
aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${AWS_REGION}"
echo "Stack deleted."

# --- Empty and delete S3 bucket ---
if [[ -n "${S3_BUCKET}" ]]; then
  if aws s3api head-bucket --bucket "${S3_BUCKET}" --region "${AWS_REGION}" 2>/dev/null; then
    echo "Emptying S3 bucket: ${S3_BUCKET}..."
    aws s3 rm "s3://${S3_BUCKET}" --recursive --region "${AWS_REGION}"
    aws s3api delete-bucket --bucket "${S3_BUCKET}" --region "${AWS_REGION}"
    echo "Bucket deleted: ${S3_BUCKET}"
  else
    echo "S3 bucket ${S3_BUCKET} not found, skipping."
  fi
fi

echo "Cleanup complete."
