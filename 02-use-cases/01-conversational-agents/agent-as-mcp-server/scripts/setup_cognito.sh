#!/bin/bash
set -euo pipefail
# ============================================================================
# Cognito setup for the agent-as-MCP-server sample
#
# Creates ONE Cognito User Pool with TWO app clients:
#
#   1. M2M client (client_credentials)  -- used by the agent on AgentCore Runtime
#      to call the *tools* Gateway. Registered in AgentCore Identity by deploy.sh.
#
#   2. User client (authorization_code + PKCE, no secret) -- used by humans in
#      MCP clients (Claude custom connector, Claude Desktop, ...) to sign in to the
#      *front-door* Gateway through the browser. Also gets a demo user.
#
# Both Gateways validate JWTs from this pool via CUSTOM_JWT authorizers, each
# restricted to its own client id.
#
# Usage: ./scripts/setup_cognito.sh [region]
# Outputs are written to .env and .cognito-state.json (used by destroy.sh).
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${1:-${AWS_REGION:-us-east-1}}"
STATE_FILE="$PROJECT_DIR/.cognito-state.json"
ENV_FILE="$PROJECT_DIR/.env"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region "$REGION")
POOL_NAME="AgentAsMcp-UserPool"
DOMAIN_PREFIX="agent-as-mcp-${ACCOUNT_ID}"
RESOURCE_SERVER_ID="agentcore"
SCOPE_NAME="invoke"
M2M_CLIENT_NAME="AgentAsMcp-M2M"
USER_CLIENT_NAME="AgentAsMcp-Users"
DEMO_USERNAME="${DEMO_USERNAME:-demo@example.com}"

# Callback URLs for MCP clients that sign in through the browser.
# Claude (claude.ai custom connectors) and Claude Desktop use these; add your own.
# localhost:3334/oauth/callback is mcp-remote's default, for clients that only support local (stdio) servers.
CALLBACK_URLS='["https://claude.ai/api/mcp/auth_callback","https://claude.com/api/mcp/auth_callback","http://127.0.0.1:53280/callback","http://localhost:53280/callback","http://localhost:3334/oauth/callback"]'

echo "🔐 Setting up Cognito for agent-as-MCP-server..."
echo "   Region: $REGION   Account: $ACCOUNT_ID"
echo ""

# ─── User Pool ─────────────────────────────────────────────────────────────
EXISTING_POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  --query "UserPools[?Name=='${POOL_NAME}'].Id | [0]" --output text 2>/dev/null || echo "None")
if [ "$EXISTING_POOL_ID" != "None" ] && [ -n "$EXISTING_POOL_ID" ]; then
  echo "   Using existing User Pool: $EXISTING_POOL_ID"
  USER_POOL_ID="$EXISTING_POOL_ID"
else
  echo "📝 Creating User Pool: $POOL_NAME"
  USER_POOL_ID=$(aws cognito-idp create-user-pool \
    --pool-name "$POOL_NAME" \
    --username-attributes email \
    --auto-verified-attributes email \
    --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true,RequireSymbols=false}' \
    --region "$REGION" \
    --query 'UserPool.Id' --output text)
  echo "   Created: $USER_POOL_ID"
fi

# ─── Resource server + scope ───────────────────────────────────────────────
aws cognito-idp create-resource-server \
  --user-pool-id "$USER_POOL_ID" \
  --identifier "$RESOURCE_SERVER_ID" \
  --name "AgentCore Gateway" \
  --scopes "ScopeName=${SCOPE_NAME},ScopeDescription=Invoke agent tools" \
  --region "$REGION" >/dev/null 2>&1 || true

# ─── Hosted UI domain ──────────────────────────────────────────────────────
aws cognito-idp create-user-pool-domain \
  --domain "$DOMAIN_PREFIX" \
  --user-pool-id "$USER_POOL_ID" \
  --region "$REGION" >/dev/null 2>&1 || true

# ─── App client 1: M2M (agent -> tools gateway) ────────────────────────────
M2M_CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${M2M_CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || echo "None")
if [ "$M2M_CLIENT_ID" = "None" ] || [ -z "$M2M_CLIENT_ID" ]; then
  echo "📝 Creating M2M app client: $M2M_CLIENT_NAME"
  M2M_CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-name "$M2M_CLIENT_NAME" \
    --generate-secret \
    --allowed-o-auth-flows client_credentials \
    --allowed-o-auth-scopes "${RESOURCE_SERVER_ID}/${SCOPE_NAME}" \
    --allowed-o-auth-flows-user-pool-client \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)
else
  echo "   Using existing M2M app client: $M2M_CLIENT_ID"
fi
M2M_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id "$USER_POOL_ID" --client-id "$M2M_CLIENT_ID" --region "$REGION" \
  --query 'UserPoolClient.ClientSecret' --output text)

# ─── App client 2: users (MCP clients -> front-door gateway) ───────────────
USER_CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${USER_CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || echo "None")
if [ "$USER_CLIENT_ID" = "None" ] || [ -z "$USER_CLIENT_ID" ]; then
  echo "📝 Creating user app client (auth code + PKCE): $USER_CLIENT_NAME"
  USER_CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$USER_POOL_ID" \
    --client-name "$USER_CLIENT_NAME" \
    --no-generate-secret \
    --allowed-o-auth-flows code \
    --allowed-o-auth-scopes openid email profile "${RESOURCE_SERVER_ID}/${SCOPE_NAME}" \
    --allowed-o-auth-flows-user-pool-client \
    --supported-identity-providers COGNITO \
    --callback-urls "$CALLBACK_URLS" \
    --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --region "$REGION" \
    --query 'UserPoolClient.ClientId' --output text)
else
  echo "   Using existing user app client: $USER_CLIENT_ID"
fi

# ─── Demo user ─────────────────────────────────────────────────────────────
DEMO_PASSWORD="${DEMO_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-14)Aa1}"
if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$DEMO_USERNAME" --region "$REGION" >/dev/null 2>&1; then
  echo "📝 Creating demo user: $DEMO_USERNAME"
  aws cognito-idp admin-create-user \
    --user-pool-id "$USER_POOL_ID" --username "$DEMO_USERNAME" \
    --user-attributes Name=email,Value="$DEMO_USERNAME" Name=email_verified,Value=true \
    --message-action SUPPRESS --region "$REGION" >/dev/null
  aws cognito-idp admin-set-user-password \
    --user-pool-id "$USER_POOL_ID" --username "$DEMO_USERNAME" \
    --password "$DEMO_PASSWORD" --permanent --region "$REGION"
else
  echo "   Demo user already exists: $DEMO_USERNAME (password unchanged)"
  DEMO_PASSWORD="(unchanged - see previous .env)"
fi

# ─── Endpoints ─────────────────────────────────────────────────────────────
HOSTED_UI="https://${DOMAIN_PREFIX}.auth.${REGION}.amazoncognito.com"
TOKEN_ENDPOINT="${HOSTED_UI}/oauth2/token"
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

# ─── State + .env ──────────────────────────────────────────────────────────
cat > "$STATE_FILE" <<EOF
{
  "created_by": "setup_cognito.sh",
  "region": "$REGION",
  "user_pool_id": "$USER_POOL_ID",
  "user_pool_name": "$POOL_NAME",
  "domain_prefix": "$DOMAIN_PREFIX",
  "m2m_client_id": "$M2M_CLIENT_ID",
  "user_client_id": "$USER_CLIENT_ID",
  "discovery_url": "$DISCOVERY_URL"
}
EOF

[ -f "$ENV_FILE" ] || cp "$PROJECT_DIR/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
set_env_var() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}
set_env_var "COGNITO_USER_POOL_ID" "$USER_POOL_ID"
set_env_var "COGNITO_DISCOVERY_URL" "$DISCOVERY_URL"
set_env_var "COGNITO_HOSTED_UI" "$HOSTED_UI"
set_env_var "AGENTCORE_GATEWAY_TOKEN_ENDPOINT" "$TOKEN_ENDPOINT"
set_env_var "AGENTCORE_GATEWAY_CLIENT_ID" "$M2M_CLIENT_ID"
set_env_var "AGENTCORE_GATEWAY_CLIENT_SECRET" "$M2M_CLIENT_SECRET"
set_env_var "AGENTCORE_GATEWAY_OAUTH_SCOPES" "${RESOURCE_SERVER_ID}/${SCOPE_NAME}"
set_env_var "FRONTDOOR_USER_CLIENT_ID" "$USER_CLIENT_ID"
set_env_var "DEMO_USERNAME" "$DEMO_USERNAME"
[ "$DEMO_PASSWORD" != "(unchanged - see previous .env)" ] && set_env_var "DEMO_PASSWORD" "$DEMO_PASSWORD"

echo ""
echo "✅ Cognito ready"
echo "   User Pool:        $USER_POOL_ID"
echo "   M2M client:       $M2M_CLIENT_ID   (agent -> tools gateway)"
echo "   User client:      $USER_CLIENT_ID   (MCP clients -> front-door gateway)"
echo "   Demo user:        $DEMO_USERNAME  /  $DEMO_PASSWORD"
echo "   Hosted UI:        $HOSTED_UI"
echo "   Values written to .env; state in .cognito-state.json"
