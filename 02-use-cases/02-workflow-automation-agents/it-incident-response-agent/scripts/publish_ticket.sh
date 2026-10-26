#!/usr/bin/env bash
# Publish a sample ticket to the SNS topic
# Usage: ./scripts/publish_ticket.sh [path/to/ticket.json]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

TICKET_FILE="${1:-$PROJECT_DIR/seed-data/sample_ticket.json}"
# Stack is deployed to us-west-2; override AWS_REGION if it points elsewhere
REGION="${DEPLOY_REGION:-us-west-2}"

# Get the SNS topic ARN from CloudFormation outputs
# The stack name follows the AgentCore CLI convention: AgentCore-<ProjectName>-<target>
STACK_NAME="${STACK_NAME:-AgentCore-ITIncidentAgent-dev}"
TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?contains(OutputKey,'TicketsTopicArn')].OutputValue" \
  --output text \
  --region "$REGION" 2>/dev/null || echo "")

if [ -z "$TOPIC_ARN" ] || [ "$TOPIC_ARN" = "None" ]; then
  echo "ERROR: Could not find TicketsTopicArn from stack $STACK_NAME"
  echo "       Make sure the stack is deployed: agentcore deploy -y --target dev"
  echo ""
  echo "       To use a different stack name: STACK_NAME=<name> ./scripts/publish_ticket.sh"
  exit 1
fi

echo "Publishing ticket to: $TOPIC_ARN"
echo "Ticket file: $TICKET_FILE"
echo ""

aws sns publish \
  --topic-arn "$TOPIC_ARN" \
  --message "$(cat "$TICKET_FILE")" \
  --region "$REGION"

echo ""
echo "✅ Ticket published! Check processing with:"
echo "  agentcore logs --since 5m"
