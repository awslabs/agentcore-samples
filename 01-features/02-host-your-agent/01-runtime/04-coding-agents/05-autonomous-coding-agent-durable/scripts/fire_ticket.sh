#!/usr/bin/env bash
# fire_ticket.sh — put a ticket-source file (optional) and emit the EventBridge event that
# triggers the orchestrator. Usage: fire_ticket.sh TICKET_ID
source "$(dirname "${BASH_SOURCE[0]}")/../deploy/lib.sh"
require_creds

TID="${1:?ticket id required}"

# If no source ticket exists, seed a default one.
if ! aws s3api head-object --bucket "$BUCKET" --key "tickets-source/${TID}.json" >/dev/null 2>&1; then
  TF=$(mktemp)
  printf '{"id":"%s","title":"Demo ticket %s","description":"Create hello.py exposing hello() that returns the string OK, plus a pytest test_hello.py. Install pytest in the sandbox and run the tests until they pass."}' "$TID" "$TID" > "$TF"
  aws s3api put-object --bucket "$BUCKET" --key "tickets-source/${TID}.json" --body "$TF" >/dev/null
  rm -f "$TF"
  ok "seeded tickets-source/${TID}.json"
fi

aws events put-events --entries "[{\"Source\":\"cagent.tickets\",\"DetailType\":\"TicketCreated\",\"Detail\":\"{\\\"ticketId\\\":\\\"${TID}\\\"}\"}]" >/dev/null
ok "emitted TicketCreated for ${TID} (orchestrator runs async; tail logs with: aws logs tail /aws/lambda/${PROJECT}-orchestrator --follow)"
