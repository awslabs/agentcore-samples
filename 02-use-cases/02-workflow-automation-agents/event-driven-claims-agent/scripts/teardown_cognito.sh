#!/bin/bash
set -euo pipefail

# ============================================================================
# Cognito User Pool Teardown
#
# Deletes Cognito resources that were created by setup_cognito.sh.
# Only runs if .cognito-state.json exists (proving this script created them).
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATE_FILE="$PROJECT_DIR/.cognito-state.json"

if [ ! -f "$STATE_FILE" ]; then
  echo "ℹ️  No .cognito-state.json found — Cognito was not created by setup_cognito.sh."
  echo "   Nothing to tear down."
  exit 0
fi

REGION=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['region'])")
USER_POOL_ID=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['user_pool_id'])")
DOMAIN_PREFIX=$(python3 -c "import json; print(json.load(open('$STATE_FILE'))['domain_prefix'])")

echo "🗑️  Tearing down Cognito resources created by setup_cognito.sh..."
echo "   Region: $REGION"
echo "   User Pool: $USER_POOL_ID"
echo ""

# Delete domain first (required before pool deletion)
echo "   Deleting domain: $DOMAIN_PREFIX"
aws cognito-idp delete-user-pool-domain \
  --domain "$DOMAIN_PREFIX" \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION" 2>/dev/null || true

# Delete user pool (cascades: resource servers, clients are deleted with pool)
echo "   Deleting user pool: $USER_POOL_ID"
aws cognito-idp delete-user-pool \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION"

# Remove state file
rm -f "$STATE_FILE"

echo ""
echo "✅ Cognito resources deleted."
echo "   Note: .env still contains the old values — update or recreate before next deploy."
