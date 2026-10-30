#!/usr/bin/env bash
#
# Deploy the HITL reviews backend (DynamoDB + API Gateway + Lambda) into the
# `insurance-claims-demo-hitl` stack. Packages Lambda code to S3, then deploys.
#
# The ReviewTasksTable logical id/name are unchanged from the table-only version
# of this stack, so the existing table is updated in place (not recreated).
#
# Usage:
#   bash reviews-backend/deploy.sh [--region us-east-1]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/setup/config.json"
TEMPLATE="$SCRIPT_DIR/reviews-backend.yaml"

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
COGNITO_STACK="insurance-claims-demo"
HITL_STACK="insurance-claims-demo-hitl"
S3_BUCKET=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --cognito-stack) COGNITO_STACK="$2"; shift 2 ;;
    --stack-name) HITL_STACK="$2"; shift 2 ;;
    --s3-bucket) S3_BUCKET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

if [[ -z "$S3_BUCKET" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
  S3_BUCKET="claims-reviews-artifacts-${ACCOUNT_ID}-${REGION}"
fi

echo "=== Insurance Claims Demo — Reviews Backend ==="
echo "Region:        $REGION"
echo "HITL Stack:    $HITL_STACK"
echo "Cognito Stack: $COGNITO_STACK"
echo "S3 Bucket:     $S3_BUCKET"
echo ""

# 1. Ensure artifacts bucket exists
echo "▸ Ensuring S3 artifacts bucket exists..."
if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null; then
  aws s3api create-bucket --bucket "$S3_BUCKET" --region "$REGION" \
    $(if [[ "$REGION" != "us-east-1" ]]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi) \
    > /dev/null
  echo "  Created: $S3_BUCKET"
else
  echo "  Exists: $S3_BUCKET"
fi

# 2. Package Lambda code → S3
echo ""
echo "▸ Packaging Lambda code → S3..."
PACKAGED_TEMPLATE="$SCRIPT_DIR/.packaged-template.yaml"
aws cloudformation package \
  --template-file "$TEMPLATE" \
  --s3-bucket "$S3_BUCKET" \
  --s3-prefix "reviews-lambda-code" \
  --output-template-file "$PACKAGED_TEMPLATE" \
  --region "$REGION" > /dev/null
echo "  Packaged template written."

# 3. Deploy
echo ""
echo "▸ Deploying CloudFormation stack: $HITL_STACK..."
aws cloudformation deploy \
  --template-file "$PACKAGED_TEMPLATE" \
  --stack-name "$HITL_STACK" \
  --parameter-overrides StackPrefix="$COGNITO_STACK" CognitoStackName="$COGNITO_STACK" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset
rm -f "$PACKAGED_TEMPLATE"
echo "  Stack deployed."

# 4. Fetch outputs
echo ""
echo "▸ Fetching outputs..."
API_URL=$(aws cloudformation describe-stacks --stack-name "$HITL_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)
TABLE_NAME=$(aws cloudformation describe-stacks --stack-name "$HITL_STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ReviewTasksTableName'].OutputValue" --output text)
echo "  API URL:    $API_URL"
echo "  Table Name: $TABLE_NAME"

# 5. SSM source-of-truth params
aws ssm put-parameter --name /insurance-claims-demo/review_tasks_table \
  --value "$TABLE_NAME" --type String --overwrite --region "$REGION" > /dev/null
aws ssm put-parameter --name /insurance-claims-demo/reviews_api_url \
  --value "$API_URL" --type String --overwrite --region "$REGION" > /dev/null
echo "  SSM params updated."

# 6. Mirror into config.json
if [[ -f "$CONFIG_FILE" ]]; then
  $PYTHON -c "
import json
c=json.load(open('$CONFIG_FILE'))
c['review_tasks_table']='$TABLE_NAME'
c['reviews_backend']={'stack_name':'$HITL_STACK','api_url':'$API_URL','table_name':'$TABLE_NAME','s3_bucket':'$S3_BUCKET'}
json.dump(c, open('$CONFIG_FILE','w'), indent=2)
print('  Updated config.json')
"
fi

echo ""
echo "✅ Reviews backend deployed!"
echo "API URL: $API_URL"
