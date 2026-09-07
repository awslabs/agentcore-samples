#!/usr/bin/env bash
#
# Cleanup — Delete all infrastructure created by deploy_all.sh.
#
# Usage:
#   bash cleanup/teardown.sh [--region us-east-1] [--yes]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/setup/config.json"

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SKIP_CONFIRM=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --yes|-y) SKIP_CONFIRM=true; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [ -f "$CONFIG_FILE" ] && [ "$REGION" = "${AWS_DEFAULT_REGION:-us-east-1}" ]; then
  REGION=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE'))['region'])" 2>/dev/null || echo "$REGION")
fi

# Known stack names (deterministic)
STACK_COGNITO="insurance-claims-demo"
STACK_REVIEWS="${STACK_COGNITO}-hitl"
STACK_SESSION="insurance-claims-session-backend"
STACK_ADMIN="insurance-claims-admin-backend"
STACK_RUNTIME="AgentCore-ClaimsAgent-default"
EXEC_ROLE_NAME="${STACK_COGNITO}-memory-execution-role"

# Memory ID is dynamic — get from config or SSM
MEMORY_ID=""
if [ -f "$CONFIG_FILE" ]; then
  MEMORY_ID=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('memory_id', ''))" 2>/dev/null || echo "")
fi
if [ -z "$MEMORY_ID" ]; then
  MEMORY_ID=$(aws ssm get-parameter --name "/insurance-claims-demo/memory_id" --region "$REGION" --query "Parameter.Value" --output text 2>/dev/null || echo "")
fi

# Resolve Python (for memory deletion SDK call)
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -f "$PROJECT_DIR/agent/src/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/agent/src/.venv/bin/python"
else
  PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
fi

echo "=== Insurance Claims Demo — Teardown ==="
echo ""
echo "Account:  $(aws sts get-caller-identity --query Account --output text --region "$REGION")"
echo "Identity: $(aws sts get-caller-identity --query Arn --output text --region "$REGION")"
echo "Region:   $REGION"
echo ""
echo "Resources to delete:"
echo "  Stacks:  $STACK_RUNTIME, $STACK_REVIEWS, $STACK_SESSION, $STACK_ADMIN, $STACK_COGNITO"
echo "  Memory:  ${MEMORY_ID:-<none found>}"
echo "  Role:    $EXEC_ROLE_NAME"
echo "  SSM:     /insurance-claims-demo/{memory_id,decision_mode,review_tasks_table,reviews_api_url}"
echo ""

if [ "$SKIP_CONFIRM" = false ]; then
  read -r -p "Type 'yes' to confirm deletion: " CONFIRM
  if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
  fi
  echo ""
fi

# 1. Delete AgentCore Memory
if [ -n "$MEMORY_ID" ]; then
  echo "▸ Deleting AgentCore Memory ($MEMORY_ID)..."
  PYTHONPATH="$PROJECT_DIR/agent/src" $PYTHON -c "
from bedrock_agentcore.memory import MemoryClient
client = MemoryClient(region_name='$REGION')
try:
    client.delete_memory_and_wait(memory_id='$MEMORY_ID', max_wait=300, poll_interval=10)
    print('  ✓ Memory deleted')
except Exception as e:
    print(f'  ⚠ {e}')
" || true
else
  echo "▸ No memory ID found — skipping"
fi

# 2. Delete stacks
for STACK in "$STACK_RUNTIME" "$STACK_REVIEWS" "$STACK_SESSION" "$STACK_ADMIN" "$STACK_COGNITO"; do
  echo "▸ Deleting stack: $STACK..."
  aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION" 2>/dev/null || true
  aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION" 2>/dev/null \
    && echo "  ✓ Deleted" || echo "  (not found or timed out)"
done

# 3. Delete IAM role
echo "▸ Deleting memory execution role..."
aws iam detach-role-policy \
  --role-name "$EXEC_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy" \
  2>/dev/null || true
aws iam delete-role --role-name "$EXEC_ROLE_NAME" 2>/dev/null \
  && echo "  ✓ Deleted" || echo "  (not found)"

# 4. Delete SSM parameters
echo "▸ Deleting SSM parameters..."
for PARAM in "/insurance-claims-demo/memory_id" \
             "/insurance-claims-demo/decision_mode" \
             "/insurance-claims-demo/review_tasks_table" \
             "/insurance-claims-demo/reviews_api_url"; do
  aws ssm delete-parameter --name "$PARAM" --region "$REGION" 2>/dev/null && echo "  ✓ $PARAM" || true
done

# 5. Remove generated files
echo "▸ Removing generated files..."
rm -f "$CONFIG_FILE" "$PROJECT_DIR/frontend/.env"

echo ""
echo "✅ Teardown complete."
