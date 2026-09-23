#!/bin/bash
set -e

# Configuration
PROJECT_NAME=${PROJECT_NAME:-agentic-sales-analyst}
REGION=${AWS_REGION:-ap-southeast-2}

echo "🐳 Deploying ECS-specific resources"
echo "Project: $PROJECT_NAME"
echo "Region: $REGION"
echo ""
echo "⚠️  You need to provide BRAVE_SEARCH_API_KEY"
read -p "Enter BRAVE_SEARCH_API_KEY: " BRAVE_API_KEY
echo ""

# Get ECR URI from infrastructure stack
ECR_URI=$(aws cloudformation describe-stacks \
    --stack-name ${PROJECT_NAME}-ecr \
    --query 'Stacks[0].Outputs[?OutputKey==`RepositoryUri`].OutputValue' \
    --output text \
    --region $REGION 2>/dev/null)

if [ -z "$ECR_URI" ]; then
    echo "❌ Error: Infrastructure not deployed. Run ../deploy-infrastructure.sh first"
    exit 1
fi

echo "Using ECR URI: $ECR_URI"

# Step 1: Deploy ECS cluster
echo ""
echo "🐳 Step 1: Deploying ECS cluster..."
aws cloudformation deploy \
    --stack-name ${PROJECT_NAME}-ecs-cluster \
    --template-file cluster.yaml \
    --parameter-overrides ProjectName=$PROJECT_NAME \
    --region $REGION

# Step 2: Deploy ECS Service
echo ""
echo "🚀 Step 2: Deploying ECS service..."

aws cloudformation deploy \
    --stack-name ${PROJECT_NAME}-ecs-service \
    --template-file service.yaml \
    --parameter-overrides \
        ProjectName=$PROJECT_NAME \
        BackendImage=$ECR_URI:backend-latest \
        FrontendImage=$ECR_URI:frontend-latest \
        BraveSearchAPIKey=$BRAVE_API_KEY \
        DesiredCount=1 \
    --region $REGION

# Get ALB URL
ALB_DNS=$(aws cloudformation describe-stacks \
    --stack-name ${PROJECT_NAME}-ecs-cluster \
    --query 'Stacks[0].Outputs[?OutputKey==`ALBDNSName`].OutputValue' \
    --output text \
    --region $REGION)

echo ""
echo "✅ ECS Deployment complete!"
echo "🌐 Application URL: http://$ALB_DNS"
echo "📊 CloudWatch Logs: /aws/bedrock-agentcore/runtimes/$PROJECT_NAME"
