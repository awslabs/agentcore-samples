#!/bin/bash
set -e

PROJECT_NAME=${PROJECT_NAME:-agentic-sales-analyst}
REGION=${AWS_REGION:-ap-southeast-2}

echo "🗑️  Cleaning up ECS-specific resources"
echo "Project: $PROJECT_NAME"
echo "Region: $REGION"

wait_for_delete() {
    local stack_name=$1
    echo "⏳ Waiting for stack $stack_name to delete..."
    aws cloudformation wait stack-delete-complete \
        --stack-name $stack_name \
        --region $REGION 2>/dev/null || true
    echo "✅ Stack $stack_name deleted"
}

empty_s3_buckets() {
    local stack_name=$1
    echo "🪣 Emptying S3 buckets in stack $stack_name..."
    
    # Get all S3 buckets from the stack
    BUCKET_NAMES=$(aws cloudformation describe-stack-resources \
        --stack-name $stack_name \
        --query 'StackResources[?ResourceType==`AWS::S3::Bucket`].PhysicalResourceId' \
        --output text \
        --region $REGION 2>/dev/null || echo "")
    
    if [ -n "$BUCKET_NAMES" ]; then
        for bucket in $BUCKET_NAMES; do
            if [ -n "$bucket" ] && [ "$bucket" != "None" ]; then
                echo "Emptying bucket: $bucket"
                aws s3 rm s3://$bucket --recursive --region $REGION 2>/dev/null || true
                echo "✅ Bucket $bucket emptied"
            fi
        done
    else
        echo "No S3 buckets found in stack $stack_name"
    fi
}

echo ""
echo "Deleting ECS service..."
aws cloudformation delete-stack \
    --stack-name ${PROJECT_NAME}-ecs-service \
    --region $REGION 2>/dev/null || true
wait_for_delete ${PROJECT_NAME}-ecs-service

echo ""
echo "Emptying S3 buckets before deleting ECS cluster..."
empty_s3_buckets ${PROJECT_NAME}-ecs-cluster

echo ""
echo "Deleting ECS cluster..."
aws cloudformation delete-stack \
    --stack-name ${PROJECT_NAME}-ecs-cluster \
    --region $REGION 2>/dev/null || true
wait_for_delete ${PROJECT_NAME}-ecs-cluster

echo ""
echo "✅ ECS cleanup complete!"
echo ""
echo "To delete shared infrastructure (network, IAM, ECR):"
echo "  cd .."
echo "  ./cleanup-infrastructure.sh"
