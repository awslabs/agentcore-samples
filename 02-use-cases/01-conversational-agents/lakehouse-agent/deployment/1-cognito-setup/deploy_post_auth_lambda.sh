#!/bin/bash

# Deploy Cognito Post-Authentication Lambda Trigger
# This script:
# 1. Creates DynamoDB table for login audit logs
# 2. Creates IAM role for Lambda
# 3. Packages and deploys Lambda function
# 4. Configures Cognito User Pool trigger

set -e

# Prints progress while the long AWS waits below run. Resolved before the `cd` further down.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/wait.sh"

# aws_or_existing <expected> <info> <aws args...> runs an aws command with its normal output.
# A failure whose error text contains <expected> (what a re-run gets for a resource that
# already exists) prints <info> and continues; any other failure prints the error and exits 1.
aws_or_existing() {
    local expected="$1"
    local info="$2"
    shift 2
    local err
    if { err=$(aws "$@" 2>&1 >&3); } 3>&1; then
        return 0
    fi
    if [[ "$err" == *"$expected"* ]]; then
        echo "$info"
        return 0
    fi
    echo "❌ aws $1 $2 failed:"
    echo "$err"
    exit 1
}

# Get AWS account and region info
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
REGION=${REGION:-us-east-1}

echo "🚀 Deploying Cognito Post-Authentication Lambda"
echo "   Account: $ACCOUNT_ID"
echo "   Region: $REGION"

# Configuration
LAMBDA_NAME="lakehouse-cognito-post-auth"
TABLE_NAME="lakehouse_user_login_audit"
ROLE_NAME="lakehouse-cognito-post-auth-role"
ERRORS=()

# Step 1: Create DynamoDB table for login audit
echo ""
echo "📊 Creating DynamoDB table: $TABLE_NAME"
aws_or_existing "(ResourceInUseException)" "ℹ️  Table already exists" dynamodb create-table \
    --table-name $TABLE_NAME \
    --attribute-definitions \
        AttributeName=user_id,AttributeType=S \
        AttributeName=login_timestamp,AttributeType=S \
    --key-schema \
        AttributeName=user_id,KeyType=HASH \
        AttributeName=login_timestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --tags Key=Application,Value=lakehouse-agent Key=Purpose,Value=login-audit \
    --region $REGION

# Wait for table to be active
wait_with_progress "DynamoDB table $TABLE_NAME active" "about 8 min" aws dynamodb wait table-exists --table-name $TABLE_NAME --region $REGION

# Enable TTL on the table (after the wait: the table must be ACTIVE first)
echo "⏰ Enabling TTL on DynamoDB table"
if ! TTL_STATUS=$(aws dynamodb describe-time-to-live \
    --table-name $TABLE_NAME \
    --query TimeToLiveDescription.TimeToLiveStatus \
    --output text \
    --region $REGION 2>&1); then
    echo "❌ aws dynamodb describe-time-to-live failed:"
    echo "$TTL_STATUS"
    exit 1
fi
if [ "$TTL_STATUS" = "ENABLED" ] || [ "$TTL_STATUS" = "ENABLING" ]; then
    echo "ℹ️  TTL already enabled"
elif ! { TTL_ERR=$(aws dynamodb update-time-to-live \
        --table-name $TABLE_NAME \
        --time-to-live-specification "Enabled=true, AttributeName=ttl" \
        --region $REGION 2>&1 >&3); } 3>&1; then
    echo "❌ aws dynamodb update-time-to-live failed:"
    echo "$TTL_ERR"
    exit 1
fi

# Step 2: Create IAM role for Lambda
echo ""
echo "🔐 Creating IAM role: $ROLE_NAME"

# Create trust policy
cat > /tmp/lambda-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create role
aws_or_existing "(EntityAlreadyExists)" "ℹ️  Role already exists" iam create-role \
    --role-name $ROLE_NAME \
    --assume-role-policy-document file:///tmp/lambda-trust-policy.json \
    --description "Role for Cognito Post-Authentication Lambda" \
    --tags Key=Application,Value=lakehouse-agent Key=Purpose,Value=cognito-post-auth-role

# Create and attach inline policy for DynamoDB access
cat > /tmp/lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem",
        "dynamodb:GetItem",
        "dynamodb:Query"
      ],
      "Resource": "arn:aws:dynamodb:${REGION}:${ACCOUNT_ID}:table/${TABLE_NAME}"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:/aws/lambda/${LAMBDA_NAME}:*"
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name $ROLE_NAME \
    --policy-name "${ROLE_NAME}-policy" \
    --policy-document file:///tmp/lambda-policy.json

# IAM is eventually consistent: a new role or policy can take a few seconds before Lambda can use it.
echo "⏳ Waiting for IAM role to propagate..."
sleep 10

# Step 3: Package Lambda function
echo ""
echo "📦 Packaging Lambda function"
cd "$(dirname "$0")"
zip -q /tmp/post-auth-lambda.zip post_auth_lambda.py

# Step 4: Deploy Lambda function
echo ""
echo "🚀 Deploying Lambda function: $LAMBDA_NAME"

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Check if Lambda exists
if aws lambda get-function --function-name $LAMBDA_NAME --region $REGION >/dev/null 2>/dev/null; then
    echo "♻️  Updating existing Lambda function"
    aws lambda update-function-code \
        --function-name $LAMBDA_NAME \
        --zip-file fileb:///tmp/post-auth-lambda.zip \
        --region $REGION >/dev/null
    
    aws lambda update-function-configuration \
        --function-name $LAMBDA_NAME \
        --environment "Variables={LOGIN_AUDIT_TABLE=${TABLE_NAME}}" \
        --region $REGION >/dev/null
else
    echo "✨ Creating new Lambda function"
    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.12 \
        --role $ROLE_ARN \
        --handler post_auth_lambda.lambda_handler \
        --zip-file fileb:///tmp/post-auth-lambda.zip \
        --timeout 10 \
        --memory-size 256 \
        --environment "Variables={LOGIN_AUDIT_TABLE=${TABLE_NAME}}" \
        --description "Cognito Post-Authentication trigger for login audit logging" \
        --tags Application=lakehouse-agent,Purpose=cognito-post-auth \
        --region $REGION >/dev/null
fi
echo "✅ Lambda function code and configuration in place"

# Step 5: Grant Cognito permission to invoke Lambda
echo ""
echo "🔑 Granting Cognito permission to invoke Lambda"

# Get User Pool ID from SSM
USER_POOL_ID=$(aws ssm get-parameter \
    --name /app/lakehouse-agent/cognito-user-pool-id \
    --query 'Parameter.Value' \
    --output text \
    --region $REGION 2>/dev/null || echo "")

if [ -z "$USER_POOL_ID" ]; then
    echo "⚠️  User Pool ID not found in SSM. Please run setup_cognito.py first."
    echo "   After that, run this command to add the trigger:"
    echo "   aws lambda add-permission --function-name $LAMBDA_NAME --statement-id cognito-trigger --action lambda:InvokeFunction --principal cognito-idp.amazonaws.com --source-arn arn:aws:cognito-idp:${REGION}:${ACCOUNT_ID}:userpool/YOUR_USER_POOL_ID"
    ERRORS+=("User Pool ID not found in SSM (/app/lakehouse-agent/cognito-user-pool-id); Cognito invoke permission not granted")
else
    # Add Lambda permission for Cognito to invoke. Remove an earlier statement first:
    # one left by an older user pool has a stale SourceArn and would block the add.
    PERMISSION_MSG="✅ Added invoke permission"
    if RM_ERR=$(aws lambda remove-permission \
        --function-name $LAMBDA_NAME \
        --statement-id cognito-trigger \
        --region $REGION 2>&1 >/dev/null); then
        PERMISSION_MSG="ℹ️  Replaced existing invoke permission"
    elif [[ "$RM_ERR" != *"(ResourceNotFoundException)"* ]]; then
        echo "❌ aws lambda remove-permission failed:"
        echo "$RM_ERR"
        exit 1
    fi
    if ! { ADD_ERR=$(aws lambda add-permission \
        --function-name $LAMBDA_NAME \
        --statement-id cognito-trigger \
        --action lambda:InvokeFunction \
        --principal cognito-idp.amazonaws.com \
        --source-arn "arn:aws:cognito-idp:${REGION}:${ACCOUNT_ID}:userpool/${USER_POOL_ID}" \
        --region $REGION 2>&1 >&3); } 3>&1; then
        echo "❌ aws lambda add-permission failed:"
        echo "$ADD_ERR"
        exit 1
    fi
    echo "$PERMISSION_MSG"
    
    echo ""
    echo "✅ Lambda deployed successfully!"
    echo ""
    echo "📋 Next steps:"
    echo "   1. Update Cognito User Pool to add Post-Authentication trigger"
    echo "   2. Run: python setup_cognito.py --add-post-auth-trigger"
    echo ""
    echo "📊 Resources created:"
    echo "   - DynamoDB Table: $TABLE_NAME"
    echo "   - Lambda Function: $LAMBDA_NAME"
    echo "   - IAM Role: $ROLE_NAME"
    echo "   - Lambda ARN: arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${LAMBDA_NAME}"
fi

# Cleanup
rm -f /tmp/lambda-trust-policy.json /tmp/lambda-policy.json /tmp/post-auth-lambda.zip

echo ""
if [ ${#ERRORS[@]} -gt 0 ]; then
    echo "❌ deploy_post_auth_lambda.sh finished with ${#ERRORS[@]} error(s):"
    for e in "${ERRORS[@]}"; do
        echo "   - $e"
    done
    exit 1
fi
echo "✨ Deployment complete!"
