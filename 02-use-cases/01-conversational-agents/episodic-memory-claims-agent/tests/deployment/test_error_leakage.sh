#!/usr/bin/env bash
#
# Test: error responses must not leak internal details, and input validation
# returns proper 400 responses instead of falling through to 500.
#
source "$(dirname "$0")/_setup.sh"

echo "--- Error leakage and input validation ---"

AMY_TOKEN=$(get_id_token "amy-admin")

LEAK_PATTERN="arn:aws|[0-9]{12}|insurance-claims-|dynamodb|bedrock-agentcore|ssm:"

# get_memory with nonexistent actor returns 200 with empty arrays (not an error)
# so we test a scenario that triggers an actual error instead
CODE=$(http_code "$ADMIN_API/admin/memory" -H "Authorization: Bearer $AMY_TOKEN")
check "get_memory no params -> 200 (empty)" "200" "$CODE"

# topK validation: non-integer -> 400
CODE=$(http_code "$ADMIN_API/admin/memory/search?query=test&topK=abc" -H "Authorization: Bearer $AMY_TOKEN")
check "search_memory invalid topK -> 400" "400" "$CODE"

BODY=$(http_body "$ADMIN_API/admin/memory/search?query=test&topK=abc" -H "Authorization: Bearer $AMY_TOKEN")
check_not_contains "search_memory invalid topK no leak" "$LEAK_PATTERN" "$BODY"
check_contains "search_memory invalid topK error message" "topK must be an integer" "$BODY"

# topK validation: valid integer works
CODE=$(http_code "$ADMIN_API/admin/memory/search?query=fraud&topK=3" -H "Authorization: Bearer $AMY_TOKEN")
check "search_memory valid topK -> 200" "200" "$CODE"

# Session title truncation: titles over 50 chars are silently truncated
BOB_TOKEN=$(get_id_token "bob-policyholder")
LONG_TITLE="This is a very long session title that exceeds the fifty character maximum limit for titles"
BODY=$(http_body "$SESSION_API/sessions" \
  -X POST \
  -H "Authorization: Bearer $BOB_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"session_title\": \"$LONG_TITLE\"}")
RETURNED_TITLE=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session',{}).get('session_title',''))" 2>/dev/null)
if [[ ${#RETURNED_TITLE} -le 50 ]]; then
  echo "  PASS: session title truncated to ${#RETURNED_TITLE} chars"
  _PASS=$((_PASS + 1))
else
  echo "  FAIL: session title not truncated (${#RETURNED_TITLE} chars)"
  _FAIL=$((_FAIL + 1))
fi

print_results
