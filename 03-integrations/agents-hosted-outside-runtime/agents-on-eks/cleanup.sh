#!/usr/bin/env bash
# Delete all AWS resources created by deploy.sh.
#
# Usage:
#   bash cleanup.sh

set -euo pipefail

# ── Load configuration ──────────────────────────────────────────────────────────
if [ -f config.env ]; then
    source config.env
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="${CLUSTER_NAME:-eks-strands-agents-demo}"
SERVICE_NAME="${SERVICE_NAME:-strands-agents-travel}"
LOG_GROUP_NAME="${LOG_GROUP_NAME:-/strands-agents/travel}"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)

echo "=== Cleanup Configuration ==="
echo "Cluster:      ${CLUSTER_NAME}"
echo "Service:      ${SERVICE_NAME}"
echo "Region:       ${AWS_REGION}"
echo ""
echo "WARNING: This will delete the EKS cluster and all associated resources."
read -p "Continue? (y/N) " confirm
if [[ "${confirm}" != "y" && "${confirm}" != "Y" ]]; then
    echo "Cancelled."
    exit 0
fi

# Uninstall Helm chart
echo ""
echo "--- Uninstalling Helm chart ---"
helm uninstall "${SERVICE_NAME}" 2>/dev/null || echo "Helm release not found"

# Delete EKS cluster (takes several minutes)
echo ""
echo "--- Deleting EKS cluster (this takes several minutes) ---"
eksctl delete cluster \
    --name "${CLUSTER_NAME}" \
    --region "${AWS_REGION}" \
    --wait || echo "Cluster deletion note: check AWS console"

# Delete IAM policy
echo ""
echo "--- Deleting IAM policy ---"
aws iam delete-policy \
    --policy-arn "arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${SERVICE_NAME}-policy" 2>/dev/null \
    || echo "IAM policy not found or already deleted"

# Delete ECR repository
echo ""
echo "--- Deleting ECR repository ---"
aws ecr delete-repository \
    --repository-name "${SERVICE_NAME}" \
    --region "${AWS_REGION}" \
    --force 2>/dev/null || echo "ECR repository not found"

# Delete CloudWatch log group
echo ""
echo "--- Deleting CloudWatch log group ---"
aws logs delete-log-group \
    --log-group-name "${LOG_GROUP_NAME}" \
    --region "${AWS_REGION}" 2>/dev/null || echo "Log group not found"

echo ""
echo "=== Cleanup complete! ==="
