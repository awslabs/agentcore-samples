#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=====================================${NC}"
echo -e "${BLUE}  Weather Workflow Deployment${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

STACK_NAME="weather-workflow-stack"
# Honour the standard AWS environment variables first — every other sample in
# this folder reads them, and `aws configure get region` ignores them, so a
# caller who only exports AWS_DEFAULT_REGION was silently deployed elsewhere.
REGION="${AWS_DEFAULT_REGION:-${AWS_REGION:-$(aws configure get region || echo "us-west-2")}}"

echo "Region: $REGION"
echo "Stack: $STACK_NAME"
echo ""

echo "Deploying CloudFormation stack..."
# `set -e` aborts the script the moment deploy fails, so a `[ $? -ne 0 ]` check
# after it can never run. Handle the failure inline instead, so the error message
# is actually printed.
if ! aws cloudformation deploy \
  --template-file cloudformation.yaml \
  --stack-name $STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --region $REGION; then
  echo -e "${RED}✗ Deployment failed${NC}"
  exit 1
fi

echo ""
echo -e "${GREEN}Retrieving outputs...${NC}"

HARNESS_ID=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='HarnessId'].OutputValue" \
  --output text)

HARNESS_ARN=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='HarnessArn'].OutputValue" \
  --output text)

DYNAMODB_TABLE=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='DynamoDBTableName'].OutputValue" \
  --output text)

STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
  --stack-name $STACK_NAME \
  --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" \
  --output text)

# Save configuration
cat > deployment_info.json <<EOF
{
  "stackName": "$STACK_NAME",
  "region": "$REGION",
  "harnessId": "$HARNESS_ID",
  "harnessArn": "$HARNESS_ARN",
  "stateMachineArn": "$STATE_MACHINE_ARN",
  "dynamoTableName": "$DYNAMODB_TABLE"
}
EOF

echo ""
echo -e "${BLUE}=====================================${NC}"
echo -e "${GREEN}✓ Deployment Complete!${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""
echo "📝 Configuration:"
echo "  Harness ID: $HARNESS_ID"
echo "  DynamoDB: $DYNAMODB_TABLE"
# `basename` splits on '/', but a state machine ARN is colon-delimited, so it
# returned the whole ARN unchanged. Take the field after the last colon.
echo "  State Machine: ${STATE_MACHINE_ARN##*:}"
echo ""
echo "🚀 Next steps:"
echo "  ./test_workflow.sh              # Interactive test"
echo "  ./test_workflow.sh --use-samples  # Use example data"
echo ""
echo "🧹 Clean up:"
echo "  ./cleanup.sh"
echo ""
