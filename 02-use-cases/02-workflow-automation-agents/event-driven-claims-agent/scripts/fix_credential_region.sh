#!/bin/bash
# Repair the cognito-gateway-m2m credential provider's discovery URL to match
# the region where the stack is actually deployed. The provider is created once
# and `agentcore add credential` is idempotent, so a region change leaves a
# stale discovery URL pointing at the old region's Cognito pool.
#
# Usage: ./scripts/fix_credential_region.sh [region]
# Reads Cognito values from .env (COGNITO_DISCOVERY_URL, AGENTCORE_GATEWAY_CLIENT_ID,
# AGENTCORE_GATEWAY_CLIENT_SECRET). The secret is never printed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${1:-us-east-1}"
PROVIDER_NAME="${AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER:-cognito-gateway-m2m}"

# Load Cognito values written by setup_cognito.sh
set -a
# shellcheck disable=SC1091
source "$PROJECT_DIR/.env"
set +a

: "${COGNITO_DISCOVERY_URL:?COGNITO_DISCOVERY_URL not set in .env}"
: "${AGENTCORE_GATEWAY_CLIENT_ID:?AGENTCORE_GATEWAY_CLIENT_ID not set in .env}"
: "${AGENTCORE_GATEWAY_CLIENT_SECRET:?AGENTCORE_GATEWAY_CLIENT_SECRET not set in .env}"

echo "Updating credential provider '$PROVIDER_NAME' in $REGION"
echo "  discoveryUrl -> $COGNITO_DISCOVERY_URL"
echo "  clientId     -> $AGENTCORE_GATEWAY_CLIENT_ID"

CONFIG=$(cat <<JSON
{
  "customOauth2ProviderConfig": {
    "oauthDiscovery": { "discoveryUrl": "$COGNITO_DISCOVERY_URL" },
    "clientId": "$AGENTCORE_GATEWAY_CLIENT_ID",
    "clientSecret": "$AGENTCORE_GATEWAY_CLIENT_SECRET"
  }
}
JSON
)

aws bedrock-agentcore-control update-oauth2-credential-provider \
  --name "$PROVIDER_NAME" \
  --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input "$CONFIG" \
  --region "$REGION" \
  --query "name" --output text

echo "✅ Credential provider updated. Verifying discovery URL..."
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name "$PROVIDER_NAME" --region "$REGION" \
  --query "oauth2ProviderConfigOutput.customOauth2ProviderConfig.oauthDiscovery.discoveryUrl" \
  --output text
