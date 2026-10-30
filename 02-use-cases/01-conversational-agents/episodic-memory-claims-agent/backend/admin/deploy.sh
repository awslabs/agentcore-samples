#!/usr/bin/env bash
#
# Deploy the admin backend (API Gateway + Lambda).
#
# Usage:
#   bash admin-backend/deploy.sh [--region us-east-1]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/setup/config.json"
TEMPLATE="$SCRIPT_DIR/admin-backend.yaml"

# Defaults
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
COGNITO_STACK="insurance-claims-demo"
ADMIN_STACK="insurance-claims-admin-backend"
S3_BUCKET=""

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --cognito-stack) COGNITO_STACK="$2"; shift 2 ;;
    --stack-name) ADMIN_STACK="$2"; shift 2 ;;
    --s3-bucket) S3_BUCKET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: $CONFIG_FILE not found. Run 0_setup_infra.sh first."
  exit 1
fi

PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
SESSION_TABLE=$($PYTHON -c "import json; print(json.load(open('$CONFIG_FILE')).get('session_backend',{}).get('table_name','insurance-claims-session-backend-sessions'))")

# Auto-create S3 bucket for Lambda artifacts if not provided
if [[ -z "$S3_BUCKET" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
  S3_BUCKET="claims-admin-artifacts-${ACCOUNT_ID}-${REGION}"
fi

echo "=== Insurance Claims Demo — Admin Backend ==="
echo "Region:         $REGION"
echo "Cognito Stack:  $COGNITO_STACK"
echo "Admin Stack:    $ADMIN_STACK"
echo "Session Table:  $SESSION_TABLE"
echo "S3 Bucket:      $S3_BUCKET"
echo ""

# 1. Ensure S3 bucket exists
echo "▸ Ensuring S3 artifacts bucket exists..."
if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null; then
  aws s3api create-bucket \
    --bucket "$S3_BUCKET" \
    --region "$REGION" \
    $(if [[ "$REGION" != "us-east-1" ]]; then echo "--create-bucket-configuration LocationConstraint=$REGION"; fi) \
    > /dev/null
  echo "  Created: $S3_BUCKET"
else
  echo "  Exists: $S3_BUCKET"
fi

# 2. Package
echo ""
echo "▸ Packaging Lambda code → S3..."

PACKAGED_TEMPLATE="$SCRIPT_DIR/.packaged-template.yaml"

aws cloudformation package \
  --template-file "$TEMPLATE" \
  --s3-bucket "$S3_BUCKET" \
  --s3-prefix "lambda-code" \
  --output-template-file "$PACKAGED_TEMPLATE" \
  --region "$REGION" \
  > /dev/null

echo "  Packaged template written."

# 3. Deploy
echo ""
echo "▸ Deploying CloudFormation stack..."

aws cloudformation deploy \
  --template-file "$PACKAGED_TEMPLATE" \
  --stack-name "$ADMIN_STACK" \
  --parameter-overrides \
    StackName="$ADMIN_STACK" \
    SessionTableName="$SESSION_TABLE" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset

echo "  Stack deployed."

# Clean up
rm -f "$PACKAGED_TEMPLATE"

# 4. Fetch outputs
echo ""
echo "▸ Fetching stack outputs..."

API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$ADMIN_STACK" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

echo "  API URL: $API_URL"

# 5. Update config.json
echo ""
echo "▸ Updating config.json..."

$PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    config = json.load(f)
config['admin_backend'] = {
    'stack_name': '$ADMIN_STACK',
    'api_url': '$API_URL',
}
with open('$CONFIG_FILE', 'w') as f:
    json.dump(config, f, indent=2)
print('  Updated config.json')
"

echo ""
echo "✅ Admin backend deployed!"
echo ""
echo "API URL: $API_URL"
echo ""
echo "Endpoints:"
echo "  GET  $API_URL/admin/mode"
echo "  POST $API_URL/admin/mode"
echo "  GET  $API_URL/admin/memory?actorId=PH-1001&sessionId=..."
echo "  GET  $API_URL/admin/sessions?actorId=PH-1001"
