#!/usr/bin/env bash
# Enable CUSTOM_JWT auth on the gateway (Auth0 or any OIDC provider).
#
# This script:
#   1. Prompts for Auth0 credentials
#   2. Creates an AgentCore credential via CLI
#   3. Updates agentcore.json gateway to CUSTOM_JWT
#   4. Deploys the changes
#
# Prerequisites:
#   - Auth0 account with an M2M application + API configured
#   - agentcore CLI installed
#   - Stack already deployed with AWS_IAM (default)
#
# Usage: ./scripts/enable-custom-jwt.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "=== Enable CUSTOM_JWT Auth (Auth0/OIDC) ==="
echo ""
echo "This will switch the gateway from AWS_IAM to CUSTOM_JWT auth."
echo "You need an Auth0 M2M application and API configured."
echo ""

# Prompt for credentials
read -p "Auth0 domain (e.g. your-tenant.us.auth0.com): " AUTH0_DOMAIN
read -p "Auth0 M2M Client ID: " AUTH0_CLIENT_ID
read -sp "Auth0 M2M Client Secret: " AUTH0_CLIENT_SECRET
echo ""
read -p "Auth0 API Audience (e.g. https://it-incident-response/api): " AUTH0_AUDIENCE
read -p "Allowed scopes (space-separated, optional, e.g. 'resolve:tickets read:incidents'): " AUTH0_SCOPES

DISCOVERY_URL="https://${AUTH0_DOMAIN}/.well-known/openid-configuration"
CREDENTIAL_NAME="auth0-m2m"

echo ""
echo "==> Step 1: Creating AgentCore credential..."
agentcore add credential \
  --name "$CREDENTIAL_NAME" \
  --type oauth \
  --discovery-url "$DISCOVERY_URL" \
  --client-id "$AUTH0_CLIENT_ID" \
  --client-secret "$AUTH0_CLIENT_SECRET"

echo ""
echo "==> Step 2: Updating gateway to CUSTOM_JWT..."
# Use python to update agentcore.json
AUTH0_SCOPES="${AUTH0_SCOPES}" python3 -c "
import json
import os

with open('agentcore/agentcore.json', 'r') as f:
    config = json.load(f)

# Build the customJwtAuthorizer block. discoveryUrl is always required.
# allowedAudience / allowedClients / allowedScopes are the claim restrictions:
#   - allowedAudience: token 'aud' claim must match one of these values
#   - allowedClients:  token 'client_id'/'azp' claim must match one of these
#   - allowedScopes:   token 'scope' claim must contain at least one of these
authorizer = {
    'discoveryUrl': '${DISCOVERY_URL}',
    'allowedAudience': ['${AUTH0_AUDIENCE}'],
    'allowedClients': ['${AUTH0_CLIENT_ID}'],
}
scopes = [s for s in os.environ.get('AUTH0_SCOPES', '').split() if s]
if scopes:
    authorizer['allowedScopes'] = scopes

for gw in config.get('agentCoreGateways', []):
    if gw['name'] == 'ITIncidentGateway':
        gw['authorizerType'] = 'CUSTOM_JWT'
        gw['customJwtAuthorizer'] = authorizer
        # Remove old client credentials if switching from CLI-added gateway
        gw.pop('enableSemanticSearch', None)
        break

with open('agentcore/agentcore.json', 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')

print('Updated agentcore.json')
if scopes:
    print('  Restricting to scopes:', ', '.join(scopes))
"

echo ""
echo "==> Step 3: Deploying..."
echo "    The Runtime will be configured with:"
echo "    GATEWAY_AUTH_MODE=CUSTOM_JWT"
echo "    OAUTH_PROVIDER_NAME=${CREDENTIAL_NAME}"
echo "    GATEWAY_AUDIENCE=${AUTH0_AUDIENCE}"
echo ""

agentcore deploy -y --target dev

echo ""
echo "=== CUSTOM_JWT auth enabled! ==="
echo ""
echo "The gateway now validates Auth0 JWTs. The agent fetches M2M tokens"
echo "via AgentCore Identity (the secret never appears in agent code)."
echo ""
echo "To revert to AWS_IAM:"
echo "  1. Edit agentcore.json: set authorizerType back to 'AWS_IAM'"
echo "  2. Remove customJwtAuthorizer block"
echo "  3. agentcore deploy -y --target dev"
echo "  4. agentcore remove credential --name auth0-m2m -y"
