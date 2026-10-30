#!/usr/bin/env bash
#
# Test: admin group enforcement at API Gateway layer.
# Verifies that only users in the 'admin' Cognito group can access admin endpoints.
#
source "$(dirname "$0")/_setup.sh"

echo "--- Auth: admin group enforcement ---"

AMY_TOKEN=$(get_id_token "amy-admin")
BOB_TOKEN=$(get_id_token "bob-policyholder")
DANA_TOKEN=$(get_id_token "dana-adjuster")

CODE=$(http_code "$ADMIN_API/admin/mode" -H "Authorization: Bearer $AMY_TOKEN")
check "Amy (admin) GET /admin/mode -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/sessions?actorId=PH-1001" -H "Authorization: Bearer $AMY_TOKEN")
check "Amy (admin) GET /admin/sessions -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/memory?actorId=PH-1001" -H "Authorization: Bearer $AMY_TOKEN")
check "Amy (admin) GET /admin/memory -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/memory/search?query=fraud&namespace=claims/" -H "Authorization: Bearer $AMY_TOKEN")
check "Amy (admin) GET /admin/memory/search -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/mode" -H "Authorization: Bearer $BOB_TOKEN")
check "Bob (policyholder) GET /admin/mode -> 403" "403" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/mode" -H "Authorization: Bearer $DANA_TOKEN")
check "Dana (adjuster) GET /admin/mode -> 403" "403" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/mode")
check "No token GET /admin/mode -> 401" "401" "$CODE"

print_results
