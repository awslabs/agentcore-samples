#!/bin/bash

# Deploy Lambda function for RTP Overlay Backend

set -e
export TMPDIR=/tmp

echo "Deploying Lambda functions..."

# Check if infrastructure stack exists
if ! aws cloudformation describe-stacks --stack-name RtpOverlayStack --region us-east-1 > /dev/null 2>&1; then
    echo "Error: Infrastructure stack not found. Deploy infrastructure first: ./deploy.sh"
    exit 1
fi

# Get infrastructure outputs
VPC_ID=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' \
    --output text)

DB_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`DatabaseEndpoint`].OutputValue' \
    --output text)

DB_SECRET_ARN=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`DatabaseSecretArn`].OutputValue' \
    --output text)

S3_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`ReceiptsBucketName`].OutputValue' \
    --output text)

# Build backend
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR/../backend"
npm install
npm run build

# Create lambda code zip
zip -r -q ../lambda-code.zip dist/ lambda.ts package.json
cd "$SCRIPT_DIR"

# Deploy Lambda stack
cdk deploy RtpOverlayLambdaStack \
    --app "npx ts-node bin/deploy-lambda.ts" \
    --context vpcId="$VPC_ID" \
    --context dbEndpoint="$DB_ENDPOINT" \
    --context dbSecretArn="$DB_SECRET_ARN" \
    --context s3BucketName="$S3_BUCKET" \
    --require-approval never

# Get API URL
API_URL=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayLambdaStack \
    --region us-east-1 \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
    --output text)

echo "✓ Lambda functions deployed: $API_URL"
