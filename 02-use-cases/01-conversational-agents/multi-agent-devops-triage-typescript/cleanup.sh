#!/usr/bin/env bash
# Removes everything deploy.sh and the CDK stack created:
#   1. The three AgentCore runtimes (lead, log-analyst, runbook)
#   2. All images in the ECR repository (so the stack can delete it)
#   3. The CDK stack (Gateway, Lambda target, IAM roles, ECR repository)
#
# Usage:
#   ./cleanup.sh [region]
set -euo pipefail

REGION="${1:-${AWS_REGION:-us-east-1}}"
STACK_NAME="SampleClaudeAgentcoreGateway"

echo "==> Deleting AgentCore runtimes"
for name in sample_lead sample_runbook sample_log_analyst; do
  runtime_id=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${name}'].agentRuntimeId" --output text)
  if [[ -n "$runtime_id" && "$runtime_id" != "None" ]]; then
    echo "    deleting ${name} (${runtime_id})"
    aws bedrock-agentcore-control delete-agent-runtime --region "$REGION" \
      --agent-runtime-id "$runtime_id" >/dev/null
  else
    echo "    ${name}: not found, skipping"
  fi
done

echo "==> Emptying ECR repository"
repo_uri=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AgentImageRepoUri'].OutputValue" --output text 2>/dev/null || true)
if [[ -n "$repo_uri" && "$repo_uri" != "None" ]]; then
  repo_name="${repo_uri#*/}"
  image_ids=$(aws ecr list-images --repository-name "$repo_name" --region "$REGION" \
    --query 'imageIds' --output json)
  if [[ "$image_ids" != "[]" ]]; then
    aws ecr batch-delete-image --repository-name "$repo_name" --region "$REGION" \
      --image-ids "$image_ids" >/dev/null
    echo "    deleted all images from ${repo_name}"
  else
    echo "    ${repo_name}: already empty"
  fi
else
  echo "    stack ${STACK_NAME} not found, skipping"
fi

echo "==> Destroying CDK stack ${STACK_NAME}"
(cd infra && npx cdk destroy --force)

echo ""
echo "Cleanup complete."
