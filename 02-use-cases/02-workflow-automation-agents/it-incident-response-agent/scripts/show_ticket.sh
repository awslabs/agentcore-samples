#!/usr/bin/env bash
# Show the resolved state of a ticket from DynamoDB.
# Usage: ./scripts/show_ticket.sh INC-20260604-001
set -euo pipefail

TICKET_ID="${1:?Usage: show_ticket.sh <ticket_id>}"
# Stack is deployed to us-west-2; override AWS_REGION if it points elsewhere
REGION="${DEPLOY_REGION:-us-west-2}"
STACK="${STACK_NAME:-AgentCore-ITIncidentAgent-dev}"

TABLE=$(aws cloudformation describe-stacks \
  --stack-name "$STACK" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?contains(OutputKey,'TicketsTableName')].OutputValue" \
  --output text 2>/dev/null || echo "")

if [ -z "$TABLE" ] || [ "$TABLE" = "None" ]; then
  echo "ERROR: Could not find TicketsTableName from stack $STACK"
  echo "       Try setting STACK_NAME to match your deployed stack."
  exit 1
fi

echo "=== Ticket: $TICKET_ID ==="
echo "Table: $TABLE"
echo ""

aws dynamodb get-item \
  --table-name "$TABLE" \
  --region "$REGION" \
  --key "{\"ticket_id\":{\"S\":\"$TICKET_ID\"}}" \
  --output json | python3 -m json.tool
