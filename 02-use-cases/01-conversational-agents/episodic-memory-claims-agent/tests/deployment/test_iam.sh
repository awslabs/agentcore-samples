#!/usr/bin/env bash
#
# Test: IAM policies scoped to specific memory resource.
# Verifies all three backends can still access memory after Resource:'*' was removed.
#
source "$(dirname "$0")/_setup.sh"

echo "--- IAM: scoped memory access ---"

AMY_TOKEN=$(get_id_token "amy-admin")
BOB_TOKEN=$(get_id_token "bob-policyholder")
DANA_TOKEN=$(get_id_token "dana-adjuster")

CODE=$(http_code "$ADMIN_API/admin/memory?actorId=PH-1001" -H "Authorization: Bearer $AMY_TOKEN")
check "Admin memory access (scoped IAM) -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/memory/search?query=fraud&namespace=claims/" -H "Authorization: Bearer $AMY_TOKEN")
check "Admin memory search (namespace condition) -> 200" "200" "$CODE"

CODE=$(http_code "$SESSION_API/sessions?user_id=PH-1001" -H "Authorization: Bearer $BOB_TOKEN")
check "Session backend (scoped IAM) -> 200" "200" "$CODE"

CODE=$(http_code "$REVIEWS_API/reviews" -H "Authorization: Bearer $DANA_TOKEN")
check "Reviews backend (scoped IAM) -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/sessions?actorId=PH-1001" -H "Authorization: Bearer $AMY_TOKEN")
check "Admin sessions GSI query -> 200" "200" "$CODE"

CODE=$(http_code "$ADMIN_API/admin/sessions" -H "Authorization: Bearer $AMY_TOKEN")
check "Admin sessions missing actorId -> 400" "400" "$CODE"

print_results
