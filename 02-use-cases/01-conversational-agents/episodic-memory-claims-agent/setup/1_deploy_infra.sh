#!/usr/bin/env bash
#
# Step 0 — Deploy infrastructure for the insurance claims demo.
#
# Deploys:
#   1. CloudFormation stack (Cognito user pool + 3 demo users)
#   2. AgentCore Memory with episodic strategy (via Python SDK — no CF resource)
#   3. Sets permanent passwords for demo users
#   4. Writes setup/config.json
#
# Usage:
#   cd insurance-claims-demo
#   bash setup/0_setup_infra.sh [--region us-east-1]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$SCRIPT_DIR/config.json"

# Defaults
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
STACK_NAME="insurance-claims-demo"
TEMPLATE="$SCRIPT_DIR/cognito.yaml"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=== Insurance Claims Demo — Setup ==="
echo "Region:     $REGION"
echo "Stack:      $STACK_NAME"
echo ""

# -----------------------------------------------------------------------
# 1. Deploy CloudFormation (Cognito)
# -----------------------------------------------------------------------
echo "▸ Deploying CloudFormation stack..."
aws cloudformation deploy \
  --template-file "$TEMPLATE" \
  --stack-name "$STACK_NAME" \
  --parameter-overrides StackPrefix="$STACK_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset

echo "▸ Fetching stack outputs..."
POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" \
  --output text)

CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='UserPoolClientId'].OutputValue" \
  --output text)

echo "  Pool ID:   $POOL_ID"
echo "  Client ID: $CLIENT_ID"

# -----------------------------------------------------------------------
# 1b. HITL reviews backend (table + API Gateway + Lambda)
#     Deployed separately (needs Lambda packaging):
#       bash reviews-backend/deploy.sh --region "$REGION"
#     It creates the insurance-claims-demo-hitl stack and writes the
#     review_tasks_table + reviews_api_url SSM params and config.json entries.
# -----------------------------------------------------------------------

# -----------------------------------------------------------------------
# 2. Set permanent passwords for demo users
#    (CF creates users in FORCE_CHANGE_PASSWORD state)
# -----------------------------------------------------------------------
echo ""
echo "▸ Setting permanent passwords for demo users..."

for ENTRY in "bob-policyholder:DemoPass1!" "alice-policyholder:DemoPass2!" "charlie-policyholder:DemoPass3!" "dana-adjuster:AdjustPass1!"; do
  USERNAME="${ENTRY%%:*}"
  PASSWORD="${ENTRY#*:}"
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$POOL_ID" \
    --username "$USERNAME" \
    --password "$PASSWORD" \
    --permanent \
    --region "$REGION" 2>/dev/null || true
  echo "  ✓ $USERNAME"
done

# -----------------------------------------------------------------------
# 3. Create AgentCore Memory (via Python SDK)
# -----------------------------------------------------------------------
echo ""
echo "▸ Creating memory execution role (for custom-strategy Bedrock inference)..."

PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -f "$PROJECT_DIR/agent/src/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/agent/src/.venv/bin/python"
else
  PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
fi
export PYTHONPATH="$PROJECT_DIR/agent/src"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
EXEC_ROLE_NAME="${STACK_NAME}-memory-execution-role"
EXEC_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${EXEC_ROLE_NAME}"

cat > /tmp/memory-trust-policy.json <<TRUST
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
    "Action": "sts:AssumeRole",
    "Condition": {
      "StringEquals": {"aws:SourceAccount": "${ACCOUNT_ID}"},
      "ArnLike": {"aws:SourceArn": "arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:*"}
    }
  }]
}
TRUST

aws iam create-role \
  --role-name "$EXEC_ROLE_NAME" \
  --assume-role-policy-document file:///tmp/memory-trust-policy.json \
  --description "Execution role for AgentCore Memory custom episodic strategy (Bedrock inference)." \
  --region "$REGION" 2>/dev/null \
  && echo "  Created role $EXEC_ROLE_NAME" \
  || aws iam update-assume-role-policy --role-name "$EXEC_ROLE_NAME" --policy-document file:///tmp/memory-trust-policy.json --region "$REGION"

aws iam attach-role-policy \
  --role-name "$EXEC_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/AmazonBedrockAgentCoreMemoryBedrockModelInferenceExecutionRolePolicy" \
  --region "$REGION" 2>/dev/null || true

echo "  Role ARN: $EXEC_ROLE_ARN"
# Give IAM a moment to propagate before the service assumes the role
sleep 10

echo ""
echo "▸ Creating AgentCore Memory with CUSTOM episodic strategy (claims-focused)..."

# Memory creation logic + custom prompts live in memory/strategy.py
# (single source of truth, shared with memory/recreate.py).
MEMORY_ID=$(EXEC_ROLE_ARN="$EXEC_ROLE_ARN" MEMORY_NAME="${STACK_NAME//-/_}_episodic_memory" REGION="$REGION" PYTHONPATH="$PROJECT_DIR/agent/src" $PYTHON -c "
import os
from bedrock_agentcore.memory import MemoryClient
from memory.strategy import create_claims_memory

client = MemoryClient(region_name=os.environ['REGION'])
mid = create_claims_memory(
    client,
    name=os.environ['MEMORY_NAME'],
    memory_execution_role_arn=os.environ['EXEC_ROLE_ARN'],
)
print(mid)
")

echo "  Memory ID: $MEMORY_ID"

# -----------------------------------------------------------------------
# 3a. Publish memory_id to SSM (single source of truth — no redeploys on recreate)
# -----------------------------------------------------------------------
echo ""
echo "▸ Publishing memory_id to SSM (/insurance-claims-demo/memory_id)..."
aws ssm put-parameter \
  --name "/insurance-claims-demo/memory_id" \
  --value "$MEMORY_ID" \
  --type String \
  --overwrite \
  --description "Current AgentCore Memory ID for insurance-claims-demo (source of truth)." \
  --region "$REGION" > /dev/null
echo "  Published."
echo ""
echo "▸ Enabling memory log delivery to CloudWatch Logs..."

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
MEMORY_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:memory/${MEMORY_ID}"

$PYTHON -c "
import boto3
from botocore.exceptions import ClientError

region = '$REGION'
memory_id = '$MEMORY_ID'
memory_arn = '$MEMORY_ARN'
logs = boto3.client('logs', region_name=region)

# CloudWatch Logs vended-logs delivery: source (memory) -> destination (log group)
log_group = f'/aws/vendedlogs/bedrock-agentcore/memory/APPLICATION_LOGS/{memory_id}'
source_name = f'{memory_id}-app-logs'[:60]
dest_name = f'{memory_id}-cwl-dest'[:60]

try:
    logs.create_log_group(logGroupName=log_group)
except ClientError as e:
    if 'ResourceAlreadyExistsException' not in str(e):
        print(f'  (log group note: {e})')

try:
    logs.put_delivery_source(
        name=source_name,
        resourceArn=memory_arn,
        logType='APPLICATION_LOGS',
    )
    dest = logs.put_delivery_destination(
        name=dest_name,
        deliveryDestinationConfiguration={
            'destinationResourceArn': f'arn:aws:logs:{region}:$ACCOUNT_ID:log-group:{log_group}:*'
        },
    )
    dest_arn = dest['deliveryDestination']['arn']
    logs.create_delivery(
        deliverySourceName=source_name,
        deliveryDestinationArn=dest_arn,
    )
    print(f'  Log delivery enabled -> {log_group}')
except ClientError as e:
    if 'ConflictException' in str(e) or 'already exists' in str(e):
        print('  Log delivery already configured.')
    else:
        print(f'  (log delivery warning: {e})')
"
echo ""
echo "▸ Writing config.json..."

cat > "$CONFIG_FILE" <<EOF
{
  "region": "$REGION",
  "stack_name": "$STACK_NAME",
  "memory_id": "$MEMORY_ID",
  "memory_name": "${STACK_NAME//-/_}_episodic_memory",
  "cognito": {
    "pool_id": "$POOL_ID",
    "client_id": "$CLIENT_ID"
  },
  "users": [
    {"username": "bob-policyholder", "password": "DemoPass1!", "actor_id": "PH-1001"},
    {"username": "alice-policyholder", "password": "DemoPass2!", "actor_id": "PH-1042"},
    {"username": "charlie-policyholder", "password": "DemoPass3!", "actor_id": "PH-1087"}
  ]
}
EOF

echo "  Written to: $CONFIG_FILE"
echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Seed episodes:  python hydration/0_seed_episodes.py"
echo "  2. Wait 10-15 min for episode extraction"
echo "  3. Run the demo:   python server.py"
