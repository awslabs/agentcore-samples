#!/usr/bin/env bash
#
# Test: CORS headers use parameterized origin.
# Verifies both Lambda responses and OPTIONS preflight return the configured origin.
#
source "$(dirname "$0")/_setup.sh"

echo "--- CORS: origin headers ---"

AMY_TOKEN=$(get_id_token "amy-admin")
BOB_TOKEN=$(get_id_token "bob-policyholder")
DANA_TOKEN=$(get_id_token "dana-adjuster")

EXPECTED_ORIGIN="*"

HEADER=$(http_header "access-control-allow-origin" "$ADMIN_API/admin/mode" -H "Authorization: Bearer $AMY_TOKEN")
check "Admin response CORS" "$EXPECTED_ORIGIN" "$HEADER"

HEADER=$(http_header "access-control-allow-origin" -X OPTIONS "$ADMIN_API/admin/mode")
check "Admin OPTIONS CORS" "$EXPECTED_ORIGIN" "$HEADER"

HEADER=$(http_header "access-control-allow-origin" "$SESSION_API/sessions?user_id=PH-1001" -H "Authorization: Bearer $BOB_TOKEN")
check "Session response CORS" "$EXPECTED_ORIGIN" "$HEADER"

HEADER=$(http_header "access-control-allow-origin" -X OPTIONS "$SESSION_API/sessions")
check "Session OPTIONS CORS" "$EXPECTED_ORIGIN" "$HEADER"

HEADER=$(http_header "access-control-allow-origin" "$REVIEWS_API/reviews" -H "Authorization: Bearer $DANA_TOKEN")
check "Reviews response CORS" "$EXPECTED_ORIGIN" "$HEADER"

HEADER=$(http_header "access-control-allow-origin" -X OPTIONS "$REVIEWS_API/reviews")
check "Reviews OPTIONS CORS" "$EXPECTED_ORIGIN" "$HEADER"

print_results
