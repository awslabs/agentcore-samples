#!/bin/bash

# Deploy Visa B2B Stub API Stack

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Deploying Visa B2B Stub APIs..."

npm run build

npx cdk deploy VisaStubStack \
  --app "npx ts-node bin/deploy-visa-stub.ts" \
  --require-approval never

# Get API URL for next steps
API_URL=$(aws cloudformation describe-stacks \
  --stack-name VisaStubStack \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`VisaStubApiUrl`].OutputValue' \
  --output text 2>/dev/null)

echo "✓ Visa B2B Stub APIs deployed: $API_URL"
