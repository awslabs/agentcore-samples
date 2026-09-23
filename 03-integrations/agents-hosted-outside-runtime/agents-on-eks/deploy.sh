#!/usr/bin/env bash
# Deploy the Strands Travel Agent to Amazon EKS with CloudWatch Gen AI Observability.
#
# Prerequisites:
#   - aws CLI installed and configured
#   - eksctl (v0.208+), helm (v3+), kubectl, docker installed
#   - Amazon Bedrock Claude model enabled in your account
#
# Usage:
#   cp config.env.example config.env  # edit as needed
#   bash deploy.sh
#
# To test after deployment:
#   python invoke.py

set -euo pipefail

# ── Load configuration ──────────────────────────────────────────────────────────
if [ -f config.env ]; then
    source config.env
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-eks-strands-agents-demo}"
SERVICE_NAME="${SERVICE_NAME:-strands-agents-travel}"
LOG_GROUP_NAME="${LOG_GROUP_NAME:-/strands-agents/travel}"
LOG_STREAM_NAME="${LOG_STREAM_NAME:-agent-logs}"
METRIC_NAMESPACE="${METRIC_NAMESPACE:-StrandsAgents/Travel}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
SERVICE_PORT="${SERVICE_PORT:-80}"

# Detect account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)

echo "=== Deployment Configuration ==="
echo "AWS Account ID: ${AWS_ACCOUNT_ID}"
echo "AWS Region:     ${AWS_REGION}"
echo "Cluster Name:   ${CLUSTER_NAME}"
echo "Service Name:   ${SERVICE_NAME}"
echo "Log Group:      ${LOG_GROUP_NAME}"
echo ""

# ── Step 1: Create CloudWatch log group and stream ──────────────────────────────
echo "--- Step 1: Create CloudWatch log group ---"
aws logs create-log-group \
    --log-group-name "${LOG_GROUP_NAME}" \
    --region "${AWS_REGION}" 2>/dev/null || echo "Log group already exists"
aws logs create-log-stream \
    --log-group-name "${LOG_GROUP_NAME}" \
    --log-stream-name "${LOG_STREAM_NAME}" \
    --region "${AWS_REGION}" 2>/dev/null || echo "Log stream already exists"
echo "CloudWatch resources ready."

# ── Step 2: Create EKS Auto Mode cluster (takes ~15-20 minutes) ────────────────
echo ""
echo "--- Step 2: Create EKS Auto Mode cluster ---"
echo "This will take approximately 15-20 minutes..."
eksctl create cluster \
    --name "${CLUSTER_NAME}" \
    --region "${AWS_REGION}" \
    --enable-auto-mode

# Configure kubeconfig
aws eks update-kubeconfig --name "${CLUSTER_NAME}" --region "${AWS_REGION}"
echo "Cluster nodes:"
kubectl get nodes

# ── Step 3: Build and push Docker image to ECR ─────────────────────────────────
echo ""
echo "--- Step 3: Build and push Docker image to ECR ---"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Authenticate to ECR
aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login --username AWS --password-stdin "${ECR_URI}"

# Create ECR repository
aws ecr create-repository \
    --repository-name "${SERVICE_NAME}" \
    --region "${AWS_REGION}" 2>/dev/null || echo "ECR repository already exists"

# Build with OTEL configuration as build args
docker build --platform linux/amd64 \
    --build-arg "SERVICE_NAME=${SERVICE_NAME}" \
    --build-arg "LOG_GROUP=${LOG_GROUP_NAME}" \
    --build-arg "LOG_STREAM=${LOG_STREAM_NAME}" \
    --build-arg "METRIC_NAMESPACE=${METRIC_NAMESPACE}" \
    -t "${SERVICE_NAME}:latest" \
    docker/

# Tag and push
docker tag "${SERVICE_NAME}:latest" "${ECR_URI}/${SERVICE_NAME}:latest"
docker push "${ECR_URI}/${SERVICE_NAME}:latest"
echo "Docker image pushed: ${ECR_URI}/${SERVICE_NAME}:latest"

# ── Step 4: Create IAM policy ──────────────────────────────────────────────────
echo ""
echo "--- Step 4: Create IAM policy ---"
POLICY_DOC=$(cat <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams"
      ],
      "Resource": "*"
    }
  ]
}
EOF
)

echo "${POLICY_DOC}" > /tmp/travel-agent-policy.json
aws iam create-policy \
    --policy-name "${SERVICE_NAME}-policy" \
    --policy-document file:///tmp/travel-agent-policy.json 2>/dev/null || echo "IAM policy already exists"
rm -f /tmp/travel-agent-policy.json
echo "IAM policy ready."

# ── Step 5: Create EKS Pod Identity ───────────────────────────────────────────
echo ""
echo "--- Step 5: Create EKS Pod Identity association ---"
eksctl create podidentityassociation \
    --cluster "${CLUSTER_NAME}" \
    --namespace default \
    --service-account-name "${SERVICE_NAME}" \
    --permission-policy-arns "arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${SERVICE_NAME}-policy" \
    --role-name "eks-${SERVICE_NAME}" \
    --region "${AWS_REGION}"
echo "Pod Identity association created."

# ── Step 6: Deploy with Helm ───────────────────────────────────────────────────
echo ""
echo "--- Step 6: Deploy with Helm ---"
helm upgrade --install "${SERVICE_NAME}" ./chart \
    --set "image.repository=${ECR_URI}/${SERVICE_NAME}" \
    --set "image.tag=latest"

echo "Waiting for deployment to be ready..."
kubectl wait --for=condition=available deployments "${SERVICE_NAME}" --timeout=300s

echo ""
echo "Pod status:"
kubectl get pods -l "app.kubernetes.io/name=${SERVICE_NAME}"

echo ""
echo "=== Deployment complete! ==="
echo ""
echo "To test the agent, run:"
echo "  kubectl port-forward service/${SERVICE_NAME} ${LOCAL_PORT}:${SERVICE_PORT} &"
echo "  python invoke.py"
echo ""
echo "View traces: CloudWatch -> Gen AI Observability"
