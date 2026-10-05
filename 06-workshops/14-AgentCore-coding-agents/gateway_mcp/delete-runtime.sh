#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

echo "==> Deleting AgentCore Runtime: ${RUNTIME_NAME}"

# 1. Find and delete the runtime
RUNTIME_ID=$(state_get "runtime_id")
if [[ -z "$RUNTIME_ID" ]]; then
  RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes \
    --region "$AWS_REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
    --output text 2>/dev/null || true)
  [[ "$RUNTIME_ID" == "None" ]] && RUNTIME_ID=""
fi

if [[ -n "$RUNTIME_ID" ]]; then
  echo "Deleting runtime '${RUNTIME_NAME}' (${RUNTIME_ID})..."
  aws bedrock-agentcore-control delete-agent-runtime \
    --agent-runtime-id "$RUNTIME_ID" \
    --region "$AWS_REGION"
  echo "Runtime deleted."
else
  echo "Runtime '${RUNTIME_NAME}' not found. Skipping."
fi

state_set "runtime_id" ""
state_set "runtime_arn" ""

# 2. Delete IAM role
echo ""
echo "--- Cleaning up IAM Role: ${IAM_ROLE_NAME} ---"
if aws iam get-role --role-name "$IAM_ROLE_NAME" &>/dev/null; then
  # Detach managed policies
  ATTACHED_POLICIES=$(aws iam list-attached-role-policies \
    --role-name "$IAM_ROLE_NAME" \
    --query 'AttachedPolicies[].PolicyArn' --output text)
  for policy_arn in $ATTACHED_POLICIES; do
    echo "  Detaching policy: ${policy_arn}"
    aws iam detach-role-policy --role-name "$IAM_ROLE_NAME" --policy-arn "$policy_arn"
  done

  # Delete inline policies
  INLINE_POLICIES=$(aws iam list-role-policies \
    --role-name "$IAM_ROLE_NAME" \
    --query 'PolicyNames[]' --output text)
  for policy_name in $INLINE_POLICIES; do
    echo "  Deleting inline policy: ${policy_name}"
    aws iam delete-role-policy --role-name "$IAM_ROLE_NAME" --policy-name "$policy_name"
  done

  echo "  Deleting role..."
  aws iam delete-role --role-name "$IAM_ROLE_NAME"
  echo "  IAM role deleted."
else
  echo "IAM role '${IAM_ROLE_NAME}' not found. Skipping."
fi

state_set "iam_role_arn" ""
state_set "iam_role_name" ""

# 3. Delete ECR repository
echo ""
echo "--- Cleaning up ECR Repository: ${ECR_REPO_NAME} ---"
if aws ecr describe-repositories --repository-names "$ECR_REPO_NAME" --region "$AWS_REGION" &>/dev/null; then
  echo "Deleting ECR repo '${ECR_REPO_NAME}' (including all images)..."
  aws ecr delete-repository \
    --repository-name "$ECR_REPO_NAME" \
    --region "$AWS_REGION" \
    --force
  echo "ECR repo deleted."
else
  echo "ECR repo '${ECR_REPO_NAME}' not found. Skipping."
fi

state_set "ecr_repo" ""
state_set "image_uri" ""

echo ""
echo "==> Runtime teardown complete."
