#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# =============================================================================
# Create a demo user in an EntraID tenant for testing the auth flow.
#
# Usage:
#   ./create-demo-user.sh --tenant-id <id> --domain <domain> <username> [password]
#
# Examples:
#   ./create-demo-user.sh --tenant-id abc123 --domain contoso.onmicrosoft.com demo1
#   ./create-demo-user.sh --tenant-id abc123 --domain contoso.onmicrosoft.com demo2 MyP@ssw0rd123
#
# Prerequisites:
#   1. Azure CLI installed and logged in:
#        az login --tenant <tenant-id> --allow-no-subscriptions
#      (--allow-no-subscriptions is needed for CIAM-only tenants)
#      The logged-in user must have User Administrator (or Global Admin) role.
#   2. jq must be installed
#
# The script uses the Azure CLI token (from `az login`) to call the Graph API.
# No application permissions needed — it runs under the admin user's identity.
# =============================================================================

set -euo pipefail

# --- Parse arguments ---
TENANT_ID=""
DOMAIN=""
USERNAME=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    -*) echo "Unknown option: $1"; exit 1 ;;
    *)
      if [ -z "$USERNAME" ]; then
        USERNAME="$1"
      elif [ -z "$PASSWORD" ]; then
        PASSWORD="$1"
      fi
      shift ;;
  esac
done

if [ -z "$TENANT_ID" ] || [ -z "$DOMAIN" ] || [ -z "$USERNAME" ]; then
  echo "Usage: $0 --tenant-id <id> --domain <domain> <username> [password]"
  echo ""
  echo "Creates a demo user: <username>@<domain>"
  echo "If no password is given, one is auto-generated."
  echo ""
  echo "Prerequisite: az login --tenant <tenant-id> --allow-no-subscriptions"
  exit 1
fi

# Auto-generate password if not provided
if [ -z "$PASSWORD" ]; then
  PASSWORD="Demo$(date +%s | shasum | head -c 8)!Aa1"  # pragma: allowlist secret
fi

EMAIL="${USERNAME}@${DOMAIN}"

echo "Creating user: ${EMAIL}"
echo "Password: ${PASSWORD}"
echo ""

# --- Step 1: Get Graph API token from Azure CLI ---
echo "→ Getting Graph API token from Azure CLI..."
ACCESS_TOKEN=$(az account get-access-token \
  --resource https://graph.microsoft.com \
  --tenant "${TENANT_ID}" \
  --query accessToken -o tsv 2>/dev/null) || {
  echo "✗ Failed to get token. Are you logged in?"
  echo ""
  echo "Run:  az login --tenant ${TENANT_ID} --allow-no-subscriptions"
  echo "  (--allow-no-subscriptions is needed for CIAM-only tenants)"
  echo "The account must have User Administrator or Global Admin role."
  exit 1
}

echo "✓ Got Graph API token (via az cli)"

# --- Step 2: Create user via Graph API ---
echo "→ Creating user..."
CREATE_RESPONSE=$(curl -s -X POST \
  "https://graph.microsoft.com/v1.0/users" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"displayName\": \"Demo User (${USERNAME})\",
    \"identities\": [
      {
        \"signInType\": \"emailAddress\",
        \"issuer\": \"${DOMAIN}\",
        \"issuerAssignedId\": \"${EMAIL}\"
      }
    ],
    \"mail\": \"${EMAIL}\",
    \"passwordProfile\": {
      \"password\": \"${PASSWORD}\",
      \"forceChangePasswordNextSignIn\": false
    },
    \"passwordPolicies\": \"DisablePasswordExpiration\"
  }")

USER_ID=$(echo "$CREATE_RESPONSE" | jq -r '.id')

if [ "$USER_ID" = "null" ] || [ -z "$USER_ID" ]; then
  ERROR_CODE=$(echo "$CREATE_RESPONSE" | jq -r '.error.code // empty')
  ERROR_MSG=$(echo "$CREATE_RESPONSE" | jq -r '.error.message // empty')
  echo "✗ Failed to create user:"
  echo "  Error: ${ERROR_CODE}"
  echo "  Message: ${ERROR_MSG}"
  exit 1
fi

echo "✓ User created"
echo ""
echo "=== Demo User Details ==="
echo "  Email:    ${EMAIL}"
echo "  Password: ${PASSWORD}"
echo "  User ID:  ${USER_ID}"
echo "  Display:  Demo User (${USERNAME})"
