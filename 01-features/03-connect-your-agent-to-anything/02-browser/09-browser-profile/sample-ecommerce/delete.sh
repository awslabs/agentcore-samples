#!/bin/bash

# Configuration
STACK_NAME="sample-ecommerce-stack"
REGION="us-east-1"

echo "Deleting CloudFormation stack: $STACK_NAME"
echo "Note: S3 bucket will NOT be deleted"
echo ""

aws cloudformation delete-stack \
  --stack-name $STACK_NAME \
  --region $REGION

echo "Stack deletion initiated"
echo "Waiting for deletion to complete..."

aws cloudformation wait stack-delete-complete \
  --stack-name $STACK_NAME \
  --region $REGION

echo ""
echo "Stack deleted successfully!"
echo "S3 bucket and files remain intact"
