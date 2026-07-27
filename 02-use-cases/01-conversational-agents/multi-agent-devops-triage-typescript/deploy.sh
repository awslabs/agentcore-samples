#!/usr/bin/env bash
# Deploys the three agents to AgentCore Runtime.
#
# Prereqs:
#   1. infra stack deployed:  cd infra && npx cdk deploy   (Gateway, ECR, roles)
#   2. docker/podman logged in to ECR, arm64 build support
#
# Usage:
#   ./deploy.sh [region]
#
# The workers run on the A2A protocol path, the lead on HTTP. All three
# share one container image; AGENT_DIR selects the workspace (same
# mechanism as docker-compose.yaml).
set -euo pipefail

REGION="${1:-${AWS_REGION:-us-east-1}}"
STACK_NAME="SampleClaudeAgentcoreGateway"
MODEL_ID="${ANTHROPIC_MODEL:-global.anthropic.claude-haiku-4-5-20251001-v1:0}"

echo "==> Reading CDK stack outputs (${STACK_NAME}, ${REGION})"
outputs=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json)
REPO_URI=$(echo "$outputs" | python3 -c "import json,sys; print([o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='AgentImageRepoUri'][0])")
GATEWAY_URL=$(echo "$outputs" | python3 -c "import json,sys; print([o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='GatewayUrl'][0])")
ROLE_ARN=$(echo "$outputs" | python3 -c "import json,sys; print([o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='RuntimeExecutionRoleArn'][0])")
echo "    repo:    $REPO_URI"
echo "    gateway: $GATEWAY_URL"

echo "==> Building and pushing linux/arm64 image"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO_URI%%/*}"
docker build --platform linux/arm64 -f docker/agent.Dockerfile -t "$REPO_URI:latest" .
docker push "$REPO_URI:latest"

# create_runtime <name> <protocol> <env-json>
create_runtime() {
  local name="$1" protocol="$2" env_json="$3"
  local existing
  existing=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query "agentRuntimes[?agentRuntimeName=='${name}'].agentRuntimeId" --output text)
  if [[ -n "$existing" && "$existing" != "None" ]]; then
    echo "==> Updating runtime ${name} (${existing})" >&2
    aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
      --agent-runtime-id "$existing" \
      --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${REPO_URI}:latest\"}}" \
      --role-arn "$ROLE_ARN" \
      --network-configuration '{"networkMode": "PUBLIC"}' \
      --protocol-configuration "{\"serverProtocol\": \"${protocol}\"}" \
      --environment-variables "$env_json" \
      --query agentRuntimeArn --output text
  else
    echo "==> Creating runtime ${name}" >&2
    aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
      --agent-runtime-name "$name" \
      --agent-runtime-artifact "{\"containerConfiguration\": {\"containerUri\": \"${REPO_URI}:latest\"}}" \
      --role-arn "$ROLE_ARN" \
      --network-configuration '{"networkMode": "PUBLIC"}' \
      --protocol-configuration "{\"serverProtocol\": \"${protocol}\"}" \
      --environment-variables "$env_json" \
      --query agentRuntimeArn --output text
  fi
}

common_env() {
  # $1 = AGENT_DIR, extras appended as ", \"K\": \"V\"" via $2
  echo "{\"AGENT_DIR\": \"$1\", \"AWS_REGION\": \"${REGION}\", \"ANTHROPIC_MODEL\": \"${MODEL_ID}\"$2}"
}

LOG_ANALYST_ARN=$(create_runtime sample_log_analyst A2A "$(common_env agents/log-analyst '')")
RUNBOOK_ARN=$(create_runtime sample_runbook A2A "$(common_env agents/runbook ", \"GATEWAY_MCP_URL\": \"${GATEWAY_URL}\"")")
LEAD_ARN=$(create_runtime sample_lead HTTP "$(common_env agents/lead ", \"LOG_ANALYST_RUNTIME_ARN\": \"${LOG_ANALYST_ARN}\", \"RUNBOOK_RUNTIME_ARN\": \"${RUNBOOK_ARN}\"")")

echo ""
echo "Deployed:"
echo "  log-analyst: $LOG_ANALYST_ARN"
echo "  runbook:     $RUNBOOK_ARN"
echo "  lead:        $LEAD_ARN"
echo ""
echo "Invoke the lead:"
echo "  ./invoke.sh '$LEAD_ARN' 'orders-api latency spiked after the 14:00 deploy…' $REGION"
