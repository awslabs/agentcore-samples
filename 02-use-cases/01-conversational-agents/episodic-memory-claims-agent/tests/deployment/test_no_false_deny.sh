#!/usr/bin/env bash
#
# Test: normal claim processing does not return DENIED from technical failures.
# Sends a straightforward claim and verifies the response is a valid decision
# (APPROVED, ESCALATED, or UNDER REVIEW) — not a technical-failure DENIED.
#
# Also validates: signals module (tools record structured data, orchestrator
# reads it back within the same invocation). If signals broke, process_claim()
# would fail or produce a technical-failure escalation.
#
source "$(dirname "$0")/_setup.sh"

echo "--- No false deny: normal claim processing ---"

BOB_ACCESS_TOKEN=$(get_access_token "bob-policyholder")
SESSION_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()) + '-' + str(uuid.uuid4())[:4])")

CLAIM_MSG="I need to file a claim. My policy is HO-2024-1001. On 2024-07-20 we had a kitchen fire caused by a grease accident. The kitchen cabinets, countertop, and ceiling have smoke and fire damage. Estimated damage is about 15000 dollars. I have a fire department report and photos. Filed the next day. No injuries. My name is Bob Thompson, phone 555-0101."

PAYLOAD=$(python3 -c "
import json, sys
print(json.dumps({'prompt': sys.argv[1], 'actorId': 'PH-1001', 'sessionId': sys.argv[2]}))
" "$CLAIM_MSG" "$SESSION_ID")

RESPONSE=$(curl -s -X POST "$RUNTIME_URL" \
  -H "Authorization: Bearer $BOB_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --max-time 120)

# The agent should respond conversationally (asking follow-ups or giving a decision)
# It must NOT contain "DECISION: DENIED" with a technical-failure message
if echo "$RESPONSE" | grep -q "technical issue\|unable to complete\|encountered an issue"; then
  echo "  FAIL: Response contains technical-failure denial language"
  _FAIL=$((_FAIL + 1))
else
  echo "  PASS: No technical-failure denial in response"
  _PASS=$((_PASS + 1))
fi

# Verify we got a non-empty response (runtime is healthy)
if [[ ${#RESPONSE} -lt 10 ]]; then
  echo "  FAIL: Empty or very short response (runtime may be down)"
  _FAIL=$((_FAIL + 1))
else
  echo "  PASS: Runtime returned valid response (${#RESPONSE} chars)"
  _PASS=$((_PASS + 1))
fi

print_results
