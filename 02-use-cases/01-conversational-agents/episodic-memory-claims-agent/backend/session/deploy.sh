#!/usr/bin/env bash
#
# Deploy the session backend (API Gateway + Lambda + DynamoDB).
#
# Uses `aws cloudformation package` to zip Lambda code, upload to S3,
# and rewrite the template — then deploys in a single atomic step.
#
# Usage:
#   cd insurance-claims-demo
#   bash session-backend/deploy.sh [--region us-east-1]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/setup/config.json"
TEMPLATE="$SCRIPT_DIR/session-backend.yaml"

# Defaults
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
COGNITO_STACK="insurance-claims-demo"
SESSION_STACK="insurance-claims-session-backend"
S3_BUCKET=""

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --cognito-stack) COGNITO_STACK="$2"; shift 2 ;;
    --stack-name) SESSION_STACK="$2"; shift 2 ;;
    --s3-bucket) S3_BUCKET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Read memory_id from config.json
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "ERROR: $CONFIG_FILE not found. Run 0_setup_infra.sh first."
  exit 1
fi

PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)

MEMORY_ID=$($PYTHON -c "import json; print(json.load(open('$CONFIG_FILE'))['memory_id'])")

# Auto-create S3 bucket for Lambda artifacts if not provided
if [[ -z "$S3_BUCKET" ]]; then
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
  S3_BUCKET="claims-session-artifacts-${ACCOUNT_ID}-${REGION}"
fi

echo "=== Insurance Claims Demo — Session Backend ==="
echo "Region:        $REGION"
echo "Cognito Stack: $COGNITO_STACK"
echo "Session Stack: $SESSION_STACK"
echo "Memory ID:     $MEMORY_ID"
echo "S3 Bucket:     $S3_BUCKET"
echo ""

# -----------------------------------------------------------------------
# 1. Ensure S3 bucket exists for Lambda artifacts
# -----------------------------------------------------------------------
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

# -----------------------------------------------------------------------
# 2. Package — zips Lambda code, uploads to S3, rewrites template
# -----------------------------------------------------------------------
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

# -----------------------------------------------------------------------
# 3. Deploy — single atomic step, real code included
# -----------------------------------------------------------------------
echo ""
echo "▸ Deploying CloudFormation stack..."

aws cloudformation deploy \
  --template-file "$PACKAGED_TEMPLATE" \
  --stack-name "$SESSION_STACK" \
  --parameter-overrides \
    StackName="$SESSION_STACK" \
    CognitoStackName="$COGNITO_STACK" \
    MemoryId="$MEMORY_ID" \
  --capabilities CAPABILITY_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset

echo "  Stack deployed."

# Clean up packaged template
rm -f "$PACKAGED_TEMPLATE"

# -----------------------------------------------------------------------
# 4. Fetch outputs
# -----------------------------------------------------------------------
echo ""
echo "▸ Fetching stack outputs..."

API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$SESSION_STACK" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

TABLE_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$SESSION_STACK" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='TableName'].OutputValue" \
  --output text)

echo "  API URL:    $API_URL"
echo "  Table Name: $TABLE_NAME"

# -----------------------------------------------------------------------
# 5. Update config.json with session backend info
# -----------------------------------------------------------------------
echo ""
echo "▸ Updating config.json..."

$PYTHON -c "
import json
with open('$CONFIG_FILE') as f:
    config = json.load(f)
config['session_backend'] = {
    'stack_name': '$SESSION_STACK',
    'api_url': '$API_URL',
    'table_name': '$TABLE_NAME',
    's3_bucket': '$S3_BUCKET'
}
with open('$CONFIG_FILE', 'w') as f:
    json.dump(config, f, indent=2)
print('  Updated config.json')
"

echo ""
echo "✅ Session backend deployed!"
echo ""
echo "API URL: $API_URL"
echo ""
echo "Test with:"
echo "  curl -H 'Authorization: Bearer <id_token>' $API_URL/sessions"
