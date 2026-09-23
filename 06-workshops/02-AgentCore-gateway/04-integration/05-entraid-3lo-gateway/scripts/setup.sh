#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
# =============================================================================
# Full automated setup: EntraID app registrations + AWS deployment.
#
# Creates both EntraID app registrations (App A + App B), the AWS OAuth
# credential provider, deploys the CDK stack, and wires everything together
# (redirect URIs, workload identity return URLs).
#
# Usage:
#   ./setup.sh \
#     --tenant-id <entra-tenant-id> \
#     --tenant-type <ciam|standard> \
#     --ciam-domain <domain>          # only for ciam tenants \
#     --region <aws-region> \
#     --stack-name <cfn-stack-name> \
#     --suffix <resource-suffix>       # optional, for parallel deployments
#
# Example (CIAM tenant):
#   ./setup.sh \
#     --tenant-id 00000000-0000-0000-0000-000000000000 \
#     --tenant-type ciam \
#     --ciam-domain your-domain \
#     --region us-east-1 \
#     --stack-name MyEntraIdStack \
#     --suffix v2
#
# Example (standard tenant):
#   ./setup.sh \
#     --tenant-id abcd1234-... \
#     --tenant-type standard \
#     --region us-east-1 \
#     --stack-name EntraIdProd
#
# Prerequisites:
#   - Azure CLI: az login --tenant <tenant-id> --allow-no-subscriptions
#   - AWS CLI v2 configured with credentials
#   - Node.js 18+, npm, CDK CLI
#   - jq installed
# =============================================================================

set -euo pipefail

# --- Defaults ---
TENANT_ID=""
TENANT_TYPE="standard"
CIAM_DOMAIN=""
AWS_REGION=""
STACK_NAME=""
SUFFIX=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CDK_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
  case $1 in
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --tenant-type) TENANT_TYPE="$2"; shift 2 ;;
    --ciam-domain) CIAM_DOMAIN="$2"; shift 2 ;;
    --region) AWS_REGION="$2"; shift 2 ;;
    --stack-name) STACK_NAME="$2"; shift 2 ;;
    --suffix) SUFFIX="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# --- Validate ---
if [ -z "$TENANT_ID" ] || [ -z "$AWS_REGION" ] || [ -z "$STACK_NAME" ]; then
  echo "Usage: $0 --tenant-id <id> --region <region> --stack-name <name> [--tenant-type ciam|standard] [--ciam-domain <domain>] [--suffix <suffix>]"
  exit 1
fi

if [ "$TENANT_TYPE" = "ciam" ] && [ -z "$CIAM_DOMAIN" ]; then
  echo "Error: --ciam-domain is required for CIAM tenants"
  exit 1
fi

# Derive authority host
if [ "$TENANT_TYPE" = "ciam" ]; then
  AUTHORITY_HOST="${CIAM_DOMAIN}.ciamlogin.com"
else
  AUTHORITY_HOST="login.microsoftonline.com"
fi
DISCOVERY_URL="https://${AUTHORITY_HOST}/${TENANT_ID}/v2.0/.well-known/openid-configuration"

PROVIDER_NAME="entraid-weather-3lo"
if [ -n "$SUFFIX" ]; then
  PROVIDER_NAME="entraid-weather-3lo-${SUFFIX}"
fi

echo "============================================="
echo "  EntraID + AWS Setup"
echo "============================================="
echo "  Tenant ID:     $TENANT_ID"
echo "  Tenant type:   $TENANT_TYPE"
echo "  Authority:     $AUTHORITY_HOST"
echo "  AWS Region:    $AWS_REGION"
echo "  Stack name:    $STACK_NAME"
echo "  Suffix:        ${SUFFIX:-<none>}"
echo "  Provider name: $PROVIDER_NAME"
echo "============================================="
echo ""

# --- Helper: get Graph API token ---
get_graph_token() {
  az account get-access-token --resource https://graph.microsoft.com --tenant "$TENANT_ID" --query accessToken -o tsv 2>/dev/null
}

# --- Step 1: Create App A (SPA, public client) ---
echo "=== Step 1: Create App A (inbound auth, SPA) ==="
TOKEN=$(get_graph_token)

APP_A_NAME="agentcore-gateway-inbound"
if [ -n "$SUFFIX" ]; then
  APP_A_NAME="agentcore-gateway-inbound-${SUFFIX}"
fi

echo "→ Creating app registration: $APP_A_NAME"
APP_A_RESPONSE=$(curl -s -X POST "https://graph.microsoft.com/v1.0/applications" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"displayName\": \"$APP_A_NAME\",
    \"signInAudience\": \"AzureADMyOrg\",
    \"api\": {
      \"requestedAccessTokenVersion\": 2
    },
    \"spa\": {
      \"redirectUris\": [\"http://localhost:33418\"]
    }
  }")

APP_A_OBJECT_ID=$(echo "$APP_A_RESPONSE" | jq -r '.id')
APP_A_CLIENT_ID=$(echo "$APP_A_RESPONSE" | jq -r '.appId')

if [ "$APP_A_OBJECT_ID" = "null" ] || [ -z "$APP_A_OBJECT_ID" ]; then
  echo "✗ Failed to create App A:"
  echo "$APP_A_RESPONSE" | jq .
  exit 1
fi
echo "✓ App A created: $APP_A_CLIENT_ID (object: $APP_A_OBJECT_ID)"

# Set Application ID URI and expose gateway.access scope
echo "→ Setting Application ID URI and exposing gateway.access scope..."
sleep 2  # Graph API needs a moment after app creation

curl -s -X PATCH "https://graph.microsoft.com/v1.0/applications/$APP_A_OBJECT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"identifierUris\": [\"api://$APP_A_CLIENT_ID\"],
    \"api\": {
      \"requestedAccessTokenVersion\": 2,
      \"oauth2PermissionScopes\": [
        {
          \"id\": \"$(uuidgen | tr '[:upper:]' '[:lower:]')\",
          \"adminConsentDescription\": \"Allows access to the AgentCore Gateway API\",
          \"adminConsentDisplayName\": \"Access MCP Gateway\",
          \"isEnabled\": true,
          \"type\": \"User\",
          \"userConsentDescription\": \"Allow access to the MCP Gateway\",
          \"userConsentDisplayName\": \"Access MCP Gateway\",
          \"value\": \"gateway.access\"
        }
      ]
    }
  }" > /dev/null

echo "✓ Exposed scope: api://$APP_A_CLIENT_ID/gateway.access"

# Create service principal for App A (required for token issuance)
echo "→ Creating service principal for App A..."
curl -s -X POST "https://graph.microsoft.com/v1.0/servicePrincipals" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"appId\": \"$APP_A_CLIENT_ID\"}" > /dev/null 2>&1 || true
echo "✓ Service principal created"

echo ""

# --- Step 2: Create App B (Web, confidential client) ---
echo "=== Step 2: Create App B (outbound auth, confidential) ==="

APP_B_NAME="agentcore-weather-api"
if [ -n "$SUFFIX" ]; then
  APP_B_NAME="agentcore-weather-api-${SUFFIX}"
fi

echo "→ Creating app registration: $APP_B_NAME"
APP_B_RESPONSE=$(curl -s -X POST "https://graph.microsoft.com/v1.0/applications" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"displayName\": \"$APP_B_NAME\",
    \"signInAudience\": \"AzureADMyOrg\",
    \"api\": {
      \"requestedAccessTokenVersion\": 2
    }
  }")

APP_B_OBJECT_ID=$(echo "$APP_B_RESPONSE" | jq -r '.id')
APP_B_CLIENT_ID=$(echo "$APP_B_RESPONSE" | jq -r '.appId')

if [ "$APP_B_OBJECT_ID" = "null" ] || [ -z "$APP_B_OBJECT_ID" ]; then
  echo "✗ Failed to create App B:"
  echo "$APP_B_RESPONSE" | jq .
  exit 1
fi
echo "✓ App B created: $APP_B_CLIENT_ID (object: $APP_B_OBJECT_ID)"

# Set Application ID URI and expose weather.read scope
echo "→ Setting Application ID URI and exposing weather.read scope..."
sleep 2

curl -s -X PATCH "https://graph.microsoft.com/v1.0/applications/$APP_B_OBJECT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"identifierUris\": [\"api://$APP_B_CLIENT_ID\"],
    \"api\": {
      \"requestedAccessTokenVersion\": 2,
      \"oauth2PermissionScopes\": [
        {
          \"id\": \"$(uuidgen | tr '[:upper:]' '[:lower:]')\",
          \"adminConsentDescription\": \"Allows reading weather data from the Weather API\",
          \"adminConsentDisplayName\": \"Read weather data\",
          \"isEnabled\": true,
          \"type\": \"User\",
          \"userConsentDescription\": \"Allow this app to read weather data on your behalf\",
          \"userConsentDisplayName\": \"Read weather data\",
          \"value\": \"weather.read\"
        }
      ]
    }
  }" > /dev/null

echo "✓ Exposed scope: api://$APP_B_CLIENT_ID/weather.read"

# Create client secret for App B
echo "→ Creating client secret for App B..."
SECRET_RESPONSE=$(curl -s -X POST "https://graph.microsoft.com/v1.0/applications/$APP_B_OBJECT_ID/addPassword" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"passwordCredential\": {\"displayName\": \"agentcore-3lo-secret\"}}")

APP_B_SECRET=$(echo "$SECRET_RESPONSE" | jq -r '.secretText')  # pragma: allowlist secret
if [ "$APP_B_SECRET" = "null" ] || [ -z "$APP_B_SECRET" ]; then
  echo "✗ Failed to create client secret:"
  echo "$SECRET_RESPONSE" | jq .
  exit 1
fi
echo "✓ Client secret created"

# Create service principal for App B
echo "→ Creating service principal for App B..."
curl -s -X POST "https://graph.microsoft.com/v1.0/servicePrincipals" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"appId\": \"$APP_B_CLIENT_ID\"}" > /dev/null 2>&1 || true
echo "✓ Service principal created"

echo ""

# --- Step 3: Create OAuth Credential Provider (AWS) ---
echo "=== Step 3: Create OAuth Credential Provider ==="

# Build the provider config based on tenant type
if [ "$TENANT_TYPE" = "ciam" ]; then
  VENDOR="CustomOauth2"
  PROVIDER_CONFIG="{\"customOauth2ProviderConfig\":{\"oauthDiscovery\":{\"discoveryUrl\":\"$DISCOVERY_URL\"},\"clientId\":\"$APP_B_CLIENT_ID\",\"clientSecret\":\"$APP_B_SECRET\"}}"
else
  VENDOR="CustomOauth2"
  # Even for standard tenants, use CustomOauth2 for explicit control over discovery URL
  PROVIDER_CONFIG="{\"customOauth2ProviderConfig\":{\"oauthDiscovery\":{\"discoveryUrl\":\"$DISCOVERY_URL\"},\"clientId\":\"$APP_B_CLIENT_ID\",\"clientSecret\":\"$APP_B_SECRET\"}}"
fi

echo "→ Creating credential provider: $PROVIDER_NAME (vendor: $VENDOR)"
CRED_RESPONSE=$(aws bedrock-agentcore-control create-oauth2-credential-provider \
  --name "$PROVIDER_NAME" \
  --credential-provider-vendor "$VENDOR" \
  --oauth2-provider-config-input "$PROVIDER_CONFIG" \
  --region "$AWS_REGION" \
  --output json 2>&1)

OAUTH_PROVIDER_ARN=$(echo "$CRED_RESPONSE" | jq -r '.credentialProviderArn')
OAUTH_SECRET_ARN=$(echo "$CRED_RESPONSE" | jq -r '.clientSecretArn.secretArn')
OAUTH_CALLBACK_URL=$(echo "$CRED_RESPONSE" | jq -r '.callbackUrl')

if [ "$OAUTH_PROVIDER_ARN" = "null" ] || [ -z "$OAUTH_PROVIDER_ARN" ]; then
  echo "✗ Failed to create credential provider:"
  echo "$CRED_RESPONSE"
  exit 1
fi
echo "✓ Credential provider created"
echo "  ARN:      $OAUTH_PROVIDER_ARN"
echo "  Secret:   $OAUTH_SECRET_ARN"
echo "  Callback: $OAUTH_CALLBACK_URL"

echo ""

# --- Step 4: Register OAuth callback URL in App B ---
echo "=== Step 4: Register callback URL in App B ==="
TOKEN=$(get_graph_token)

echo "→ Adding redirect URI to App B: $OAUTH_CALLBACK_URL"
curl -s -X PATCH "https://graph.microsoft.com/v1.0/applications/$APP_B_OBJECT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"web\": {\"redirectUris\": [\"$OAUTH_CALLBACK_URL\"]}}" > /dev/null

echo "✓ Redirect URI registered in App B"
echo ""

# --- Step 5: Deploy CDK Stack ---
echo "=== Step 5: Deploy CDK Stack ==="
echo "→ Installing dependencies..."
npm install --prefix "$CDK_DIR" --silent 2>/dev/null

# Create deployment-specific OpenAPI spec with placeholder URL (updated after deploy)
OPENAPI_SOURCE="$CDK_DIR/openapi/weather-api.json"
OPENAPI_FILE="$CDK_DIR/openapi/weather-api-${STACK_NAME}.json"

INIT_SPEC=$(jq --arg tenant_id "$TENANT_ID" \
  --arg authority_host "$AUTHORITY_HOST" \
  --arg app_b_id "$APP_B_CLIENT_ID" \
  '.servers[0].url = "https://placeholder.execute-api.region.amazonaws.com" |
   .components.securitySchemes.entraId.flows.authorizationCode.authorizationUrl = "https://\($authority_host)/\($tenant_id)/oauth2/v2.0/authorize" |
   .components.securitySchemes.entraId.flows.authorizationCode.tokenUrl = "https://\($authority_host)/\($tenant_id)/oauth2/v2.0/token" |
   .components.securitySchemes.entraId.flows.authorizationCode.scopes = {"api://\($app_b_id)/weather.read": "Read weather data"} |
   .paths["/weather"].get.security[0].entraId = ["api://\($app_b_id)/weather.read"]' \
  "$OPENAPI_SOURCE")
echo "$INIT_SPEC" > "$OPENAPI_FILE"

echo "→ Deploying stack: $STACK_NAME"

# Build CDK context args
CDK_CONTEXT="-c stackName=$STACK_NAME"
CDK_CONTEXT="$CDK_CONTEXT -c entra:tenantId=$TENANT_ID"
CDK_CONTEXT="$CDK_CONTEXT -c entra:appAClientId=$APP_A_CLIENT_ID"
CDK_CONTEXT="$CDK_CONTEXT -c entra:appBClientId=$APP_B_CLIENT_ID"
CDK_CONTEXT="$CDK_CONTEXT -c entra:tenantType=$TENANT_TYPE"
CDK_CONTEXT="$CDK_CONTEXT -c oauth:providerArn=$OAUTH_PROVIDER_ARN"
CDK_CONTEXT="$CDK_CONTEXT -c oauth:secretArn=$OAUTH_SECRET_ARN"
CDK_CONTEXT="$CDK_CONTEXT -c oauth:callbackUrl=$OAUTH_CALLBACK_URL"
CDK_CONTEXT="$CDK_CONTEXT -c oauth:providerName=$PROVIDER_NAME"

if [ -n "$CIAM_DOMAIN" ]; then
  CDK_CONTEXT="$CDK_CONTEXT -c entra:ciamDomain=$CIAM_DOMAIN"
fi
if [ -n "$SUFFIX" ]; then
  CDK_CONTEXT="$CDK_CONTEXT -c resourceSuffix=$SUFFIX"
fi

CDK_CONTEXT="$CDK_CONTEXT -c openapi:path=$OPENAPI_FILE"

# Check if an OIDC provider already exists for this issuer (same tenant = same issuer)
ISSUER_HOST_FOR_OIDC=""
if [ "$TENANT_TYPE" = "ciam" ]; then
  ISSUER_HOST_FOR_OIDC="${TENANT_ID}.ciamlogin.com"
else
  ISSUER_HOST_FOR_OIDC="login.microsoftonline.com"
fi
OIDC_ISSUER_URL="${ISSUER_HOST_FOR_OIDC}/${TENANT_ID}/v2.0"

EXISTING_OIDC_ARN=$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[].Arn" --output text --region "$AWS_REGION" 2>/dev/null | tr '\t' '\n' | while read arn; do
  URL=$(aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$arn" --query "Url" --output text 2>/dev/null)
  if [ "$URL" = "$OIDC_ISSUER_URL" ]; then
    echo "$arn"
    break
  fi
done)

if [ -n "$EXISTING_OIDC_ARN" ]; then
  echo "  (Reusing existing OIDC provider: $EXISTING_OIDC_ARN)"
  CDK_CONTEXT="$CDK_CONTEXT -c oidc:providerArn=$EXISTING_OIDC_ARN"
  # Ensure our App A client ID is in the OIDC provider's audience list
  aws iam add-client-id-to-open-id-connect-provider \
    --open-id-connect-provider-arn "$EXISTING_OIDC_ARN" \
    --client-id "$APP_A_CLIENT_ID" \
    --region "$AWS_REGION" 2>/dev/null || true
  echo "  (Ensured App A client ID in OIDC audience list)"
fi

# Deploy
CDK_DEFAULT_REGION="$AWS_REGION" npx cdk deploy $STACK_NAME --require-approval never $CDK_CONTEXT --app "npx ts-node --prefer-ts-exts bin/cdk.ts" --output "cdk.out-${STACK_NAME}" 2>&1 | tee /tmp/cdk-deploy-$$.log

# Extract outputs from CloudFormation
echo ""
echo "→ Reading stack outputs..."
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" --query "Stacks[0].Outputs" --output json)

API_ENDPOINT=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="ApiEndpoint") | .OutputValue')
GATEWAY_ID=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="GatewayId") | .OutputValue')

if [ -z "$API_ENDPOINT" ] || [ "$API_ENDPOINT" = "null" ]; then
  echo "✗ Could not read stack outputs. Check CloudFormation console."
  exit 1
fi

echo "✓ Stack deployed"
echo "  API Endpoint: $API_ENDPOINT"
echo "  Gateway ID:   $GATEWAY_ID"
echo ""

# --- Step 6: Register API Gateway redirect URIs in App A ---
echo "=== Step 6: Register redirect URIs in App A ==="
TOKEN=$(get_graph_token)

# Read existing SPA redirect URIs and add the new ones
EXISTING_URIS=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "https://graph.microsoft.com/v1.0/applications/$APP_A_OBJECT_ID" | jq -r '.spa.redirectUris // []')

NEW_URIS=$(echo "$EXISTING_URIS" | jq \
  --arg cb "$API_ENDPOINT/callback" \
  --arg auth "$API_ENDPOINT/auth" \
  '. + [$cb, $auth] | unique')

echo "→ Adding SPA redirect URIs to App A:"
echo "  - $API_ENDPOINT/callback (VS Code OAuth callback)"
echo "  - $API_ENDPOINT/auth (auth onboarding SPA)"

curl -s -X PATCH "https://graph.microsoft.com/v1.0/applications/$APP_A_OBJECT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"spa\": {\"redirectUris\": $NEW_URIS}}" > /dev/null

echo "✓ Redirect URIs registered in App A"
echo ""

# --- Step 7: Update workload identity return URLs ---
echo "=== Step 7: Update workload identity return URLs ==="
echo "→ Setting allowed return URL: $API_ENDPOINT/auth/callback"

aws bedrock-agentcore-control update-workload-identity \
  --name "$GATEWAY_ID" \
  --allowed-resource-oauth2-return-urls "[\"$API_ENDPOINT/auth/callback\"]" \
  --region "$AWS_REGION" > /dev/null 2>&1

echo "✓ Workload identity updated"
echo ""

# --- Step 8: Update OpenAPI spec and redeploy ---
echo "=== Step 8: Update OpenAPI spec with API endpoint ==="

# Create a deployment-specific copy of the OpenAPI spec (preserves the original)
OPENAPI_SOURCE="$CDK_DIR/openapi/weather-api.json"
OPENAPI_FILE="$CDK_DIR/openapi/weather-api-${STACK_NAME}.json"
cp "$OPENAPI_SOURCE" "$OPENAPI_FILE"

# Update server URL and security scheme URLs
UPDATED_SPEC=$(jq --arg url "$API_ENDPOINT" \
  --arg tenant_id "$TENANT_ID" \
  --arg authority_host "$AUTHORITY_HOST" \
  --arg app_b_id "$APP_B_CLIENT_ID" \
  '.servers[0].url = $url |
   .components.securitySchemes.entraId.flows.authorizationCode.authorizationUrl = "https://\($authority_host)/\($tenant_id)/oauth2/v2.0/authorize" |
   .components.securitySchemes.entraId.flows.authorizationCode.tokenUrl = "https://\($authority_host)/\($tenant_id)/oauth2/v2.0/token" |
   .components.securitySchemes.entraId.flows.authorizationCode.scopes = {"api://\($app_b_id)/weather.read": "Read weather data"} |
   .paths["/weather"].get.security[0].entraId = ["api://\($app_b_id)/weather.read"]' \
  "$OPENAPI_FILE")

echo "$UPDATED_SPEC" > "$OPENAPI_FILE"
echo "✓ OpenAPI spec created: $OPENAPI_FILE"

echo "→ Redeploying stack with updated OpenAPI spec..."
CDK_DEFAULT_REGION="$AWS_REGION" npx cdk deploy $STACK_NAME --require-approval never $CDK_CONTEXT --app "npx ts-node --prefer-ts-exts bin/cdk.ts" --output "cdk.out-${STACK_NAME}" 2>&1 | tee -a /tmp/cdk-deploy-$$.log

echo "✓ Redeployment complete"
echo ""

# --- Done ---
echo "============================================="
echo "  Setup Complete"
echo "============================================="
echo ""
echo "  API Endpoint:    $API_ENDPOINT"
echo "  Auth Onboarding: $API_ENDPOINT/auth"
echo "  MCP Endpoint:    $API_ENDPOINT/mcp"
echo "  Gateway ID:      $GATEWAY_ID"
echo ""
echo "  App A Client ID: $APP_A_CLIENT_ID"
echo "  App B Client ID: $APP_B_CLIENT_ID"
echo "  OAuth Provider:  $PROVIDER_NAME"
echo ""
echo "  VS Code MCP config:"
echo "  {"
echo "    \"servers\": {"
echo "      \"agentcore-weather-entraid\": {"
echo "        \"type\": \"http\","
echo "        \"url\": \"$API_ENDPOINT/mcp\","
echo "        \"headers\": { \"MCP-Protocol-Version\": \"2025-11-25\" }"
echo "      }"
echo "    }"
echo "  }"
echo ""
# Get tenant domain for the demo user hint
TENANT_DOMAIN=$(curl -s -H "Authorization: Bearer $(get_graph_token)" \
  "https://graph.microsoft.com/v1.0/domains?\$top=1" | jq -r '.value[0].id // empty')
if [ -z "$TENANT_DOMAIN" ]; then
  TENANT_DOMAIN="<your-domain>.onmicrosoft.com"
fi

echo "  To create demo users:"
echo "    ./scripts/create-demo-user.sh --tenant-id $TENANT_ID --domain $TENANT_DOMAIN <username>"
echo ""
echo "  Test at: $API_ENDPOINT/auth"
echo ""

# --- Step 9: Generate env file for redeploy-cdk.sh ---
ENV_FILE="$CDK_DIR/.env.${STACK_NAME}"
cat > "$ENV_FILE" <<EOF
# Generated by setup.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# Stack: $STACK_NAME
STACK_NAME=$STACK_NAME
AWS_REGION=$AWS_REGION
ENTRA_TENANT_ID=$TENANT_ID
ENTRA_TENANT_TYPE=$TENANT_TYPE
ENTRA_CIAM_DOMAIN=$CIAM_DOMAIN
ENTRA_APP_A_CLIENT_ID=$APP_A_CLIENT_ID
ENTRA_APP_B_CLIENT_ID=$APP_B_CLIENT_ID
OAUTH_PROVIDER_ARN=$OAUTH_PROVIDER_ARN
OAUTH_SECRET_ARN=$OAUTH_SECRET_ARN
OAUTH_CALLBACK_URL=$OAUTH_CALLBACK_URL
OAUTH_PROVIDER_NAME=$PROVIDER_NAME
RESOURCE_SUFFIX=$SUFFIX
OPENAPI_PATH=$(python3 -c "import os.path; print(os.path.relpath('$OPENAPI_FILE', '$CDK_DIR'))")
OIDC_PROVIDER_ARN=${EXISTING_OIDC_ARN:-}
API_ENDPOINT=$API_ENDPOINT
GATEWAY_ID=$GATEWAY_ID
EOF

echo "  Env file: $ENV_FILE"
echo "  Redeploy: ./scripts/redeploy-cdk.sh $ENV_FILE"
echo ""
echo "============================================="
