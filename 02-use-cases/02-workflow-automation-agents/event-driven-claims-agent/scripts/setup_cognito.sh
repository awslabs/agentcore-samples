#!/bin/bash
set -euo pipefail

# ============================================================================
# Cognito User Pool Setup for Claims Agent Gateway Auth
#
# Creates a Cognito User Pool + M2M App Client for the MCP Gateway's
# CUSTOM_JWT authorizer. Run this BEFORE deploy.sh if you don't have an
# existing Cognito pool.
#
# Usage: ./scripts/setup_cognito.sh [region]
#
# Outputs are written to .env (AGENTCORE_GATEWAY_* variables).
# A state file (.cognito-state.json) tracks what was created for teardown.
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${1:-${AWS_REGION:-us-west-2}}"
STATE_FILE="$PROJECT_DIR/.cognito-state.json"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
POOL_NAME="ClaimsAgent-UserPool"
DOMAIN_PREFIX="claims-agent-${ACCOUNT_ID}"
RESOURCE_SERVER_ID="agentcore"
SCOPE_NAME="invoke"
CLIENT_NAME="ClaimsAgent-M2M"

echo "🔐 Setting up Cognito User Pool for Gateway auth..."
echo "   Region: $REGION"
echo "   Account: $ACCOUNT_ID"
echo ""

# Check if pool already exists
EXISTING_POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" --output text 2>/dev/null || echo "None")

if [ "$EXISTING_POOL_ID" != "None" ] && [ -n "$EXISTING_POOL_ID" ]; then
  echo "⚠️  User Pool '$POOL_NAME' already exists (ID: $EXISTING_POOL_ID)"
  echo "   Using existing pool. Delete it first if you want a fresh one."
  USER_POOL_ID="$EXISTING_POOL_ID"
else
  # Create User Pool
  echo "📝 Creating User Pool: $POOL_NAME"
  USER_POOL_ID=$(aws cognito-idp create-user-pool \
    --pool-name "$POOL_NAME" \
    --region "$REGION" \
    --query 'UserPool.Id' --output text)
  echo "   Created: $USER_POOL_ID"
fi

# Create or verify Resource Server
echo "📝 Configuring Resource Server: $RESOURCE_SERVER_ID"
aws cognito-idp create-resource-server \
  --user-pool-id "$USER_POOL_ID" \
  --identifier "$RESOURCE_SERVER_ID" \
  --name "AgentCore Gateway" \
  --scopes "ScopeName=${SCOPE_NAME},ScopeDescription=Invoke agent tools" \
  --region "$REGION" 2>/dev/null || true

# Create or verify Domain
echo "📝 Configuring domain: $DOMAIN_PREFIX"
aws cognito-idp create-user-pool-domain \
  --domain "$DOMAIN_PREFIX" \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION" 2>/dev/null || true

# Check for existing app client
EXISTING_CLIENT_ID=$(aws cognito-idp list-user-pool-clients \
  --user-pool-id "$USER_POOL_ID" --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || echo "None")

if [ "$EXISTING_CLIENT_ID" != "None" ] && [ -n "$EXISTING_CLIENT_ID" ]; then
  CLIENT_ID="$EXISTING_CLIENT_ID"
  echo "   Using existing app client: $CLIENT_ID"
else
  # Create App Client (M2M with client_credentials)
  echo "📝 Creating M2M App Client: $CLIENT_NAME"
  CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-name "$CLIENT_NAME" \
    --generate-secret \
    --allowed-o-auth-flows "client_credentials" \
    --allowed-o-auth-scopes "${RESOURCE_SERVER_ID}/${SCOPE_NAME}" \
    --allowed-o-auth-flows-user-pool-client \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)
  echo "   Created client: $CLIENT_ID"
fi

# Get client secret
CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$CLIENT_ID" \
  --region "$REGION" \
  --query 'UserPoolClient.ClientSecret' --output text)

# Compute endpoints
TOKEN_ENDPOINT="https://${DOMAIN_PREFIX}.auth.${REGION}.amazoncognito.com/oauth2/token"
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

# Write state file (for teardown)
cat > "$STATE_FILE" <<EOF
{
  "created_by": "setup_cognito.sh",
  "region": "$REGION",
  "user_pool_id": "$USER_POOL_ID",
  "user_pool_name": "$POOL_NAME",
  "client_id": "$CLIENT_ID",
  "domain_prefix": "$DOMAIN_PREFIX",
  "token_endpoint": "$TOKEN_ENDPOINT",
  "discovery_url": "$DISCOVERY_URL"
}
EOF

# Update .env file with Cognito values
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$PROJECT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
fi

# Helper: set or update a key in .env
set_env_var() {
  local key="$1" value="$2" file="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    # macOS-compatible sed (no -i'' with newline issues)
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file" && rm -f "${file}.bak"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

set_env_var "AGENTCORE_GATEWAY_TOKEN_ENDPOINT" "$TOKEN_ENDPOINT" "$ENV_FILE"
set_env_var "AGENTCORE_GATEWAY_CLIENT_ID" "$CLIENT_ID" "$ENV_FILE"
set_env_var "AGENTCORE_GATEWAY_CLIENT_SECRET" "$CLIENT_SECRET" "$ENV_FILE"
set_env_var "AGENTCORE_GATEWAY_OAUTH_SCOPES" "${RESOURCE_SERVER_ID}/${SCOPE_NAME}" "$ENV_FILE"
set_env_var "COGNITO_DISCOVERY_URL" "$DISCOVERY_URL" "$ENV_FILE"
set_env_var "COGNITO_USER_POOL_ID" "$USER_POOL_ID" "$ENV_FILE"

echo ""
echo "✅ Cognito setup complete!"
echo ""
echo "   User Pool ID:    $USER_POOL_ID"
echo "   Client ID:       $CLIENT_ID"
echo "   Token Endpoint:  $TOKEN_ENDPOINT"
echo "   Discovery URL:   $DISCOVERY_URL"
echo ""
echo "   Values written to: .env"
echo "   State saved to:    .cognito-state.json (used by teardown)"
