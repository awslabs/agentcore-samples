#!/bin/bash

# Configuration
STACK_NAME="sample-ecommerce-stack"
REGION="us-east-1"

echo "Getting bucket name from stack..."

# Get bucket name from existing stack
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
  --output text)

if [ -z "$BUCKET" ]; then
  echo "Error: Could not find stack or bucket name"
  exit 1
fi

echo "Uploading updated files to S3 bucket: $BUCKET"

# Upload files to S3
aws s3 sync . s3://$BUCKET/ \
  --exclude "*.yaml" \
  --exclude "*.sh" \
  --exclude "*.md" \
  --exclude ".git/*" \
  --region $REGION

# Get CloudFront distribution ID
DISTRIBUTION_ID=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`DistributionId`].OutputValue' \
  --output text)

echo "Invalidating CloudFront cache..."

# Invalidate CloudFront cache
aws cloudfront create-invalidation \
  --distribution-id $DISTRIBUTION_ID \
  --paths "/*" \
  --region $REGION

echo ""
echo "Update complete!"
echo "Files uploaded to: $BUCKET"
echo "CloudFront cache invalidated"
echo ""
echo "Note: Cache invalidation may take a few minutes to propagate"
