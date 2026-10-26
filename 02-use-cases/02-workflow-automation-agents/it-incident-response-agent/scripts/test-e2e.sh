#!/usr/bin/env bash
# End-to-end integration test suite for the IT Incident Response Agent.
#
# Tests multiple ticket types to exercise both model tiers:
#   - LOW priority  → routed to fast model (Haiku)
#   - HIGH priority → routed to full model (Sonnet)
#
# After all tickets resolve, runs on-demand evaluation against the traces.
#
# Usage:
#   ./scripts/test-e2e.sh              # full suite (3 tickets + eval)
#   ./scripts/test-e2e.sh --quick      # single ticket, no eval
#   ./scripts/test-e2e.sh --no-eval    # all tickets, skip evaluation
#
# Exit codes:
#   0 = all tests passed
#   1 = one or more tests failed
#
# Requires: AWS credentials, deployed stack (./scripts/deploy.sh)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${AWS_REGION:-us-west-2}"
POLL_INTERVAL=10
MAX_WAIT=120  # per ticket
RUN_EVAL=true
QUICK_MODE=false

# Parse flags
for arg in "$@"; do
  case "$arg" in
    --quick)    QUICK_MODE=true; RUN_EVAL=false ;;
    --no-eval)  RUN_EVAL=false ;;
    --help|-h)  head -18 "$0" | tail -16; exit 0 ;;
  esac
done

# ─── Resolve stack resources ────────────────────────────────────────
STACK_NAME="AgentCore-ITIncidentAgent-dev"

TOPIC_ARN=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`TicketsTopic`)].OutputValue' \
  --output text 2>/dev/null)

TABLE_NAME=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs[?contains(OutputKey,`TicketsTable`)].OutputValue' \
  --output text 2>/dev/null)

if [[ -z "$TOPIC_ARN" || -z "$TABLE_NAME" ]]; then
  echo "❌ Could not resolve stack outputs. Is the stack deployed?"
  echo "   Run: ./scripts/deploy.sh"
  exit 1
fi

# ─── Test tickets (LOW triggers Haiku, HIGH triggers Sonnet) ────────
TIMESTAMP=$(date +%Y%m%d%H%M%S)

# LOW priority — simple issue, routed to fast model (Haiku)
TICKET_LOW=$(cat <<JSON
{
  "ticket_id": "E2E-LOW-$TIMESTAMP",
  "requester_id": "U-1002",
  "title": "Outlook search not working",
  "description": "Search box in Outlook on macOS shows no results for any query. Restarted app, same issue.",
  "priority": "LOW",
  "category": "email"
}
JSON
)

# HIGH priority — complex multi-system issue, routed to full model (Sonnet)
TICKET_HIGH=$(cat <<JSON
{
  "ticket_id": "E2E-HIGH-$TIMESTAMP",
  "requester_id": "U-1003",
  "title": "Cannot access shared drive and VPN keeps disconnecting",
  "description": "Since this morning I cannot map \\\\\\\\files.acme.corp\\\\finance via VPN. The VPN also disconnects every 15 minutes. I need access for quarter-end reporting. Running macOS 14.5 with corp-vpn 4.2.1. This is the third time this month.",
  "priority": "HIGH",
  "category": "network"
}
JSON
)

# CRITICAL priority — urgent, exercises escalation logic
TICKET_CRITICAL=$(cat <<JSON
{
  "ticket_id": "E2E-CRIT-$TIMESTAMP",
  "requester_id": "U-1001",
  "title": "Production deployment pipeline blocked by permissions error",
  "description": "Our CI/CD pipeline fails with 'AccessDenied' on the deploy step. This is blocking a critical hotfix for a customer-facing outage. Build ID: 4521. Error: AssumeRole on arn:aws:iam::123456789012:role/deploy-prod. Started 30 minutes ago.",
  "priority": "CRITICAL",
  "category": "software"
}
JSON
)

# ─── Helper functions ───────────────────────────────────────────────

publish_ticket() {
  local ticket_json="$1"
  local ticket_id="$2"
  aws sns publish \
    --topic-arn "$TOPIC_ARN" \
    --message "$ticket_json" \
    --region "$REGION" \
    --query 'MessageId' --output text 2>/dev/null
}

wait_for_resolution() {
  local ticket_id="$1"
  local elapsed=0

  while [[ $elapsed -lt $MAX_WAIT ]]; do
    local status
    status=$(aws dynamodb get-item \
      --table-name "$TABLE_NAME" \
      --key "{\"ticket_id\": {\"S\": \"$ticket_id\"}}" \
      --projection-expression "#s" \
      --expression-attribute-names '{"#s": "status"}' \
      --region "$REGION" \
      --query 'Item.status.S' --output text 2>/dev/null || echo "None")

    if [[ "$status" == "Resolved" || "$status" == "Failed" ]]; then
      echo "$status"
      return
    fi

    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
  done

  echo "Timeout"
}

get_resolution() {
  local ticket_id="$1"
  aws dynamodb get-item \
    --table-name "$TABLE_NAME" \
    --key "{\"ticket_id\": {\"S\": \"$ticket_id\"}}" \
    --projection-expression "resolution_comment" \
    --region "$REGION" \
    --query 'Item.resolution_comment.S' --output text 2>/dev/null
}

get_error() {
  local ticket_id="$1"
  aws dynamodb get-item \
    --table-name "$TABLE_NAME" \
    --key "{\"ticket_id\": {\"S\": \"$ticket_id\"}}" \
    --projection-expression "error_message" \
    --region "$REGION" \
    --query 'Item.error_message.S' --output text 2>/dev/null
}

# ─── Run tests ──────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════════════════════"
echo "  E2E Integration Test Suite — IT Incident Response Agent"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Stack:    $STACK_NAME"
echo "  Region:   $REGION"
echo "  Run ID:   $TIMESTAMP"
echo "  Mode:     $(if $QUICK_MODE; then echo 'quick (1 ticket)'; else echo 'full (3 tickets)'; fi)"
echo "  Eval:     $(if $RUN_EVAL; then echo 'yes'; else echo 'skipped'; fi)"
echo ""

PASS=0
FAIL=0
TICKET_IDS=()

run_test() {
  local label="$1"
  local priority="$2"
  local ticket_json="$3"
  local ticket_id="$4"

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  TEST: $label"
  echo "  Ticket: $ticket_id | Priority: $priority | Model: $(if [[ $priority == 'LOW' ]]; then echo 'Haiku (fast)'; else echo 'Sonnet (full)'; fi)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Publish
  local msg_id
  msg_id=$(publish_ticket "$ticket_json" "$ticket_id")
  echo "  ▶ Published (MessageId: $msg_id)"
  TICKET_IDS+=("$ticket_id")

  # Wait
  echo "  ⏳ Waiting for resolution (max ${MAX_WAIT}s)..."
  local start_time=$SECONDS
  local result
  result=$(wait_for_resolution "$ticket_id")
  local duration=$((SECONDS - start_time))

  # Assert
  if [[ "$result" == "Resolved" ]]; then
    local resolution
    resolution=$(get_resolution "$ticket_id")
    if [[ -n "$resolution" && "$resolution" != "None" ]]; then
      echo "  ✅ PASS — Resolved in ${duration}s"
      echo "  📝 ${resolution:0:150}..."
      PASS=$((PASS + 1))
    else
      echo "  ⚠️  WARN — Status=Resolved but empty resolution_comment"
      FAIL=$((FAIL + 1))
    fi
  elif [[ "$result" == "Failed" ]]; then
    local error
    error=$(get_error "$ticket_id")
    echo "  ❌ FAIL — Agent returned error after ${duration}s"
    echo "  Error: ${error:0:200}"
    FAIL=$((FAIL + 1))
  else
    echo "  ❌ FAIL — Timed out after ${MAX_WAIT}s"
    FAIL=$((FAIL + 1))
  fi
  echo ""
}

# Test 1: LOW priority → fast model (Haiku)
run_test "Simple ticket (fast model)" "LOW" "$TICKET_LOW" "E2E-LOW-$TIMESTAMP"

if ! $QUICK_MODE; then
  # Test 2: HIGH priority → full model (Sonnet)
  run_test "Complex ticket (full model)" "HIGH" "$TICKET_HIGH" "E2E-HIGH-$TIMESTAMP"

  # Test 3: CRITICAL priority → full model + escalation
  run_test "Critical ticket (escalation)" "CRITICAL" "$TICKET_CRITICAL" "E2E-CRIT-$TIMESTAMP"
fi

# ─── Evaluation (optional) ──────────────────────────────────────────

if $RUN_EVAL && [[ $PASS -gt 0 ]]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  EVALUATION: Running on-demand scoring"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  Running evaluate.py against latest trace..."
  echo "  (Online evaluation also scores continuously via CloudWatch)"
  echo ""

  # Run the on-demand evaluator — it picks the latest trace automatically
  if python3 "$SCRIPT_DIR/evaluate.py" 2>&1 | tee /tmp/e2e-eval-output.txt; then
    echo ""
    echo "  ✅ Evaluation completed successfully"
  else
    echo ""
    echo "  ⚠️  Evaluation failed (non-blocking — may need Transaction Search enabled)"
  fi
  echo ""

  echo "  📊 Online evaluation (continuous) also scores all traces automatically:"
  echo "     • GoalSuccessRate"
  echo "     • Correctness"
  echo "     • Helpfulness"
  echo "     • ToolSelectionAccuracy"
  echo "     View in: CloudWatch → GenAI Observability → ITIncidentAgent"
  echo ""
fi

# ─── Summary ────────────────────────────────────────────────────────

echo "═══════════════════════════════════════════════════════════════"
echo "  RESULTS: $PASS passed, $FAIL failed ($(( PASS + FAIL )) total)"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "  Tickets created this run:"
for tid in "${TICKET_IDS[@]}"; do
  echo "    • $tid"
done
echo ""
echo "  Inspect: ./scripts/show_ticket.sh <ticket_id>"
echo "  Logs:    agentcore logs --since 10m"
echo "  Traces:  agentcore traces list"
echo ""

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
exit 0
