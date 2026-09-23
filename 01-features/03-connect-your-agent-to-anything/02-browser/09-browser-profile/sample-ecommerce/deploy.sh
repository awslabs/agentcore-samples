#!/bin/bash

# Configuration
STACK_NAME="sample-ecommerce-stack"
BUCKET_NAME="sample-ecommerce-static-site-$(date +%s)"
REGION="${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"

echo "Creating S3 bucket if it doesn't exist..."

# Create bucket (ignore error if already exists)
aws s3 mb s3://$BUCKET_NAME --region $REGION 2>/dev/null || echo "Bucket already exists or using existing bucket"

# Enable versioning and block public access
aws s3api put-public-access-block \
  --bucket $BUCKET_NAME \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true" \
  --region $REGION 2>/dev/null || true

echo "Deploying CloudFormation stack..."

# Deploy CloudFormation stack
aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name $STACK_NAME \
  --parameter-overrides BucketName=$BUCKET_NAME \
  --region $REGION \
  --no-fail-on-empty-changeset

# Get bucket name from stack
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`BucketName`].OutputValue' \
  --output text)

echo "Uploading website files to S3..."

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

echo "Creating CloudFront invalidation..."

# Invalidate CloudFront cache
aws cloudfront create-invalidation \
  --distribution-id $DISTRIBUTION_ID \
  --paths "/*" \
  --region $REGION

# Get CloudFront URL
CLOUDFRONT_URL=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query 'Stacks[0].Outputs[?OutputKey==`CloudFrontURL`].OutputValue' \
  --output text)

echo ""
echo "Deployment complete!"
echo "CloudFront URL: $CLOUDFRONT_URL"
echo "Bucket Name: $BUCKET"
echo ""
echo "Note: CloudFront distribution may take 10-15 minutes to fully deploy"
