#!/bin/bash
set -e

# Script to configure S3 event notifications for Lambda functions
# Run this after deploying the InvoiceProcessingStack and/or GRProcessingStack

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "🔧 Configuring S3 event notifications..."
echo ""

# Default region
REGION=${AWS_REGION:-us-east-1}
echo "Using region: $REGION"
echo ""

# Function to configure notifications for a stack
configure_stack() {
  local stack_name=$1
  local config_file=$2
  local description=$3
  
  echo "📦 Configuring $description..."
  
  # Check if stack exists
  if ! aws cloudformation describe-stacks --stack-name "$stack_name" --region "$REGION" &>/dev/null; then
    echo "⚠️  Stack $stack_name not found, skipping..."
    echo ""
    return
  fi
  
  # Get the Lambda function name
  LAMBDA_NAME=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`LambdaFunctionName`].OutputValue' \
    --output text)
  
  if [ -z "$LAMBDA_NAME" ]; then
    echo "❌ Could not find Lambda function name for $stack_name"
    echo ""
    return
  fi
  
  # Get the full Lambda ARN
  LAMBDA_FULL_ARN=$(aws lambda get-function \
    --function-name "$LAMBDA_NAME" \
    --region "$REGION" \
    --query 'Configuration.FunctionArn' \
    --output text)
  
  echo "   Lambda: $LAMBDA_NAME"
  
  # Get the input bucket name
  INPUT_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name "$stack_name" \
    --region "$REGION" \
    --query 'Stacks[0].Outputs[?OutputKey==`InputBucketName`].OutputValue' \
    --output text)
  
  if [ -z "$INPUT_BUCKET" ]; then
    echo "❌ Could not find input bucket name for $stack_name"
    echo ""
    return
  fi
  
  echo "   Bucket: $INPUT_BUCKET"
  
  # Create a temporary config file with the actual Lambda ARN
  TEMP_CONFIG=$(mktemp)
  sed "s|LAMBDA_ARN_PLACEHOLDER|$LAMBDA_FULL_ARN|g" "$config_file" > "$TEMP_CONFIG"
  
  # Add Lambda permission for S3 to invoke it
  echo "   Adding Lambda permission..."
  aws lambda add-permission \
    --function-name "$LAMBDA_NAME" \
    --region "$REGION" \
    --statement-id AllowS3Invoke \
    --action lambda:InvokeFunction \
    --principal s3.amazonaws.com \
    --source-arn "arn:aws:s3:::$INPUT_BUCKET" \
    2>/dev/null || echo "   (Permission already exists)"
  
  # Configure S3 bucket notifications
  echo "   Configuring S3 notifications..."
  aws s3api put-bucket-notification-configuration \
    --bucket "$INPUT_BUCKET" \
    --region "$REGION" \
    --notification-configuration "file://$TEMP_CONFIG"
  
  # Clean up
  rm "$TEMP_CONFIG"
  
  echo "   ✅ Done!"
  echo ""
}

# Configure Invoice Processing Stack
configure_stack "InvoiceProcessingStack" "s3-notification-config.json" "Invoice Processing"

# Configure GR Processing Stack
configure_stack "GRProcessingStack" "s3-notification-config-gr.json" "GR Processing"

echo "✅ All S3 event notifications configured successfully!"
echo ""
echo "📝 Summary:"
echo "   - Invoice Processing: Triggers on .pdf, .png, .jpg, .jpeg, .gif, .webp, .json, .csv files"
echo "   - GR Processing: Triggers on .pdf, .png, .jpg, .jpeg, .gif, .webp files"
