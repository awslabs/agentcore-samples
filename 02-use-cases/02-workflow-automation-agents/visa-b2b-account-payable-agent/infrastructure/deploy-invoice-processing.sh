#!/bin/bash

# Deploy Invoice Processing Stack

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Deploying Invoice Processing..."

npm run build

# Get API URL from Lambda stack
if [ -z "$RTP_API_URL" ]; then
  RTP_API_URL=$(aws cloudformation describe-stacks \
    --stack-name RtpOverlayLambdaStack \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
    --output text \
    --region us-east-1 2>/dev/null)
  
  if [ -z "$RTP_API_URL" ]; then
    echo "Error: Could not fetch API URL from RtpOverlayLambdaStack"
    exit 1
  fi
  export RTP_API_URL
fi

npx cdk deploy InvoiceProcessingStack \
  --app "npx ts-node bin/deploy-invoice-processing.ts" \
  --require-approval never \
  --context rtpApiUrl="$RTP_API_URL"

echo "✓ Invoice Processing deployed"
