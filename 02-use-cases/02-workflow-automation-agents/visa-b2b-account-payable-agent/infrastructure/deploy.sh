#!/bin/bash

# Deploy RTP Overlay Infrastructure

set -e
export TMPDIR=/tmp

echo "Deploying infrastructure..."

# Check AWS credentials
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "Error: AWS credentials not configured"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Bootstrap CDK if needed
if ! aws cloudformation describe-stacks --stack-name CDKToolkit --region us-east-1 > /dev/null 2>&1; then
    echo "Bootstrapping CDK..."
    cdk bootstrap aws://${ACCOUNT_ID}/us-east-1
fi

npm install
npm run build
cdk deploy --require-approval never

echo "✓ Infrastructure deployed"
