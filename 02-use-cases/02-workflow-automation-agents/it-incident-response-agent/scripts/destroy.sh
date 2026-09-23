#!/usr/bin/env bash
# Tear down all deployed resources.
# Usage: ./scripts/destroy.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

REGION="${AWS_REGION:-us-west-2}"
STACK_NAME="${STACK_NAME:-AgentCore-ITIncidentAgent-dev}"

echo "=== IT Incident Response Agent — Destroy ==="
echo ""
echo "Stack: $STACK_NAME"
echo "Region: $REGION"
echo ""
echo "This will PERMANENTLY DELETE all deployed resources including:"
echo "  - AgentCore Runtime, Gateway, Memory"
echo "  - DynamoDB tables (all data will be lost)"
echo "  - S3 buckets and contents"
echo "  - Lambda functions"
echo "  - SNS topic, EventBridge bus"
echo "  - CloudWatch log groups and alarms"
echo ""
read -p "Are you sure? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "Deleting CloudFormation stack..."
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --region "$REGION"

echo "Waiting for stack deletion to complete..."
echo "(This typically takes 3-5 minutes)"
echo ""

# Poll every 20 seconds
while true; do
  STATUS=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query 'Stacks[0].StackStatus' \
    --output text 2>/dev/null || echo "DELETE_COMPLETE")

  case "$STATUS" in
    DELETE_COMPLETE)
      echo "✅ Stack deleted successfully."
      break
      ;;
    DELETE_FAILED)
      echo "❌ Stack deletion failed. Check the AWS CloudFormation console for details."
      echo ""
      echo "Common fix: delete retained resources manually, then retry:"
      echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION"
      exit 1
      ;;
    DELETE_IN_PROGRESS)
      printf "."
      sleep 20
      ;;
    *)
      printf "."
      sleep 20
      ;;
  esac
done

echo ""
echo "=== Cleanup complete ==="
echo ""
echo "To redeploy fresh: agentcore deploy -y --target dev"
