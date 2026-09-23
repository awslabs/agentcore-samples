#!/bin/bash
set -e

# Configuration
PROJECT_NAME=${PROJECT_NAME:-agentic-sales-analyst}
REGION=${AWS_REGION:-ap-southeast-2}

echo "🚀 Deploying Common Infrastructure"
echo "Project: $PROJECT_NAME"
echo "Region: $REGION"

# Function to wait for stack completion
# amazonq-ignore-next-line
# amazonq-ignore-next-line
wait_for_stack() {
    local stack_name=$1
    echo "⏳ Waiting for stack $stack_name to complete..."
    aws cloudformation wait stack-create-complete \
        --stack-name $stack_name \
        --region $REGION 2>/dev/null || \
    aws cloudformation wait stack-update-complete \
        --stack-name $stack_name \
        --region $REGION 2>/dev/null
    echo "✅ Stack $stack_name completed"
}

# Step 1: Deploy Network
echo ""
echo "📡 Step 1: Deploying network infrastructure..."
# amazonq-ignore-next-line
aws cloudformation deploy \
    --stack-name ${PROJECT_NAME}-network \
    --template-file common/01-network.yaml \
    --parameter-overrides ProjectName=$PROJECT_NAME \
    --region $REGION

# Step 2: Deploy IAM
echo ""
echo "👤 Step 2: Deploying IAM roles..."
aws cloudformation deploy \
    --stack-name ${PROJECT_NAME}-iam \
    --template-file common/02-iam.yaml \
    --parameter-overrides ProjectName=$PROJECT_NAME \
    --capabilities CAPABILITY_NAMED_IAM \
    --region $REGION

# Step 3: Deploy ECR
echo ""
echo "📦 Step 3: Deploying ECR repository..."
aws cloudformation deploy \
    --stack-name ${PROJECT_NAME}-ecr \
    --template-file common/03-ecr.yaml \
    --parameter-overrides ProjectName=$PROJECT_NAME \
    --region $REGION

# Step 4: Build and Push Images
echo ""
echo "🏗️ Step 4: Building and pushing container images..."
ECR_URI=$(aws cloudformation describe-stacks \
    --stack-name ${PROJECT_NAME}-ecr \
    --query 'Stacks[0].Outputs[?OutputKey==`RepositoryUri`].OutputValue' \
    --output text \
    --region $REGION)

if [ -z "$ECR_URI" ]; then
    echo "❌ Error: Could not retrieve ECR repository URI"
    exit 1
fi

echo "ECR URI: $ECR_URI"

ACCOUNT_ID=$(echo $ECR_URI | cut -d'.' -f1)
if [ -z "$ACCOUNT_ID" ]; then
    echo "❌ Error: Could not extract AWS account ID from ECR URI"
    exit 1
fi

echo "Logging into ECR..."
if ! aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com; then
    echo "❌ Error: Failed to login to ECR"
    echo "Please check your AWS credentials and permissions"
    exit 1
fi
echo "✅ Successfully logged into ECR"

# Check if buildx is available
if ! docker buildx version > /dev/null 2>&1; then
    echo "❌ Error: docker buildx is not available"
    echo "Please install Docker Desktop or upgrade Docker Engine to 19.03+"
    exit 1
fi

# Create buildx builder if it doesn't exist
if ! docker buildx inspect multiarch-builder > /dev/null 2>&1; then
    echo "Creating buildx builder..."
    docker buildx create --name multiarch-builder --use
else
    echo "Using existing buildx builder..."
    docker buildx use multiarch-builder
fi

# amazonq-ignore-next-line
# Build and push PostgreSQL image
echo "Building PostgreSQL image for linux/amd64..."
docker buildx build --platform linux/amd64 -f ../Dockerfile.postgres -t $ECR_URI:postgres-latest --push ../

# Build and push backend
echo "Building backend image for linux/amd64..."
docker buildx build --platform linux/amd64 -t $ECR_URI:backend-latest --push ../

# Build and push frontend
echo "Building frontend image for linux/amd64..."
docker buildx build --platform linux/amd64 -t $ECR_URI:frontend-latest --push ../client

echo ""
echo "✅ Infrastructure deployment complete!"
echo "📦 ECR URI: $ECR_URI"
echo ""
echo "Next steps:"
echo "  ECS: cd ecs && ./deploy-ecs.sh"
echo "  EKS: cd eks && ./deploy-k8s.sh"
