#!/bin/bash

# Deploy Cognito Post-Authentication Lambda Trigger
# This script:
# 1. Creates DynamoDB table for login audit logs
# 2. Creates IAM role for Lambda
# 3. Packages and deploys Lambda function
# 4. Configures Cognito User Pool trigger

set -e

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

# Step 1: Create DynamoDB table for login audit
echo ""
echo "📊 Creating DynamoDB table: $TABLE_NAME"
aws dynamodb create-table \
    --table-name $TABLE_NAME \
    --attribute-definitions \
        AttributeName=user_id,AttributeType=S \
        AttributeName=login_timestamp,AttributeType=S \
    --key-schema \
        AttributeName=user_id,KeyType=HASH \
        AttributeName=login_timestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST \
    --tags Key=Project,Value=lakehouse-agent Key=Component,Value=audit \
    --region $REGION 2>/dev/null || echo "ℹ️  Table already exists"

# Enable TTL on the table
echo "⏰ Enabling TTL on DynamoDB table"
aws dynamodb update-time-to-live \
    --table-name $TABLE_NAME \
    --time-to-live-specification "Enabled=true, AttributeName=ttl" \
    --region $REGION 2>/dev/null || echo "ℹ️  TTL already enabled"

# Wait for table to be active
echo "⏳ Waiting for table to be active..."
aws dynamodb wait table-exists --table-name $TABLE_NAME --region $REGION

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
aws iam create-role \
    --role-name $ROLE_NAME \
    --assume-role-policy-document file:///tmp/lambda-trust-policy.json \
    --description "Role for Cognito Post-Authentication Lambda" \
    2>/dev/null || echo "ℹ️  Role already exists"

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
if aws lambda get-function --function-name $LAMBDA_NAME --region $REGION 2>/dev/null; then
    echo "♻️  Updating existing Lambda function"
    aws lambda update-function-code \
        --function-name $LAMBDA_NAME \
        --zip-file fileb:///tmp/post-auth-lambda.zip \
        --region $REGION
    
    aws lambda update-function-configuration \
        --function-name $LAMBDA_NAME \
        --environment "Variables={LOGIN_AUDIT_TABLE=${TABLE_NAME}}" \
        --region $REGION
else
    echo "✨ Creating new Lambda function"
    aws lambda create-function \
        --function-name $LAMBDA_NAME \
        --runtime python3.11 \
        --role $ROLE_ARN \
        --handler post_auth_lambda.lambda_handler \
        --zip-file fileb:///tmp/post-auth-lambda.zip \
        --timeout 10 \
        --memory-size 256 \
        --environment "Variables={LOGIN_AUDIT_TABLE=${TABLE_NAME}}" \
        --description "Cognito Post-Authentication trigger for login audit logging" \
        --region $REGION
fi

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
else
    # Add Lambda permission for Cognito to invoke
    aws lambda add-permission \
        --function-name $LAMBDA_NAME \
        --statement-id cognito-trigger \
        --action lambda:InvokeFunction \
        --principal cognito-idp.amazonaws.com \
        --source-arn "arn:aws:cognito-idp:${REGION}:${ACCOUNT_ID}:userpool/${USER_POOL_ID}" \
        --region $REGION 2>/dev/null || echo "ℹ️  Permission already exists"
    
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
echo "✨ Deployment complete!"
