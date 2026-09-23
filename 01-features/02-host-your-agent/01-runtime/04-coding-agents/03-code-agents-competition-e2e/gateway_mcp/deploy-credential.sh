#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

SECRET_NAME="agentcore/github-mcp/github-app"

echo "==> Deploying GitHub App Secret to Secrets Manager: ${SECRET_NAME}"

# Validate required env vars
if [[ -z "${GITHUB_APP_ID:-}" ]]; then
  echo "ERROR: Set GITHUB_APP_ID (the numeric ID of your GitHub App)."
  exit 1
fi

if [[ -z "${GITHUB_APP_PRIVATE_KEY_FILE:-}" ]]; then
  echo "ERROR: Set GITHUB_APP_PRIVATE_KEY_FILE (path to .pem file)."
  exit 1
fi

if [[ ! -f "$GITHUB_APP_PRIVATE_KEY_FILE" ]]; then
  echo "ERROR: Private key file not found: ${GITHUB_APP_PRIVATE_KEY_FILE}"
  exit 1
fi

if [[ -z "${GITHUB_APP_INSTALLATION_ID:-}" ]]; then
  echo "ERROR: Set GITHUB_APP_INSTALLATION_ID (the installation ID for your org/repo)."
  exit 1
fi

# Read the private key
PRIVATE_KEY=$(cat "$GITHUB_APP_PRIVATE_KEY_FILE")

# Build the secret JSON value
SECRET_VALUE=$(jq -n \
  --arg app_id "$GITHUB_APP_ID" \
  --arg private_key "$PRIVATE_KEY" \
  --arg installation_id "$GITHUB_APP_INSTALLATION_ID" \
  '{app_id: $app_id, private_key: $private_key, installation_id: $installation_id}')

# Check if secret already exists
EXISTING=$(aws secretsmanager describe-secret \
  --secret-id "$SECRET_NAME" \
  --region "$AWS_REGION" 2>/dev/null || true)

if [[ -n "$EXISTING" ]]; then
  echo "Secret '${SECRET_NAME}' already exists. Updating..."
  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_NAME" \
    --secret-string "$SECRET_VALUE" \
    --region "$AWS_REGION"
  SECRET_ARN=$(echo "$EXISTING" | jq -r '.ARN')
else
  echo "Creating secret '${SECRET_NAME}'..."
  CREATE_RESPONSE=$(aws secretsmanager create-secret \
    --name "$SECRET_NAME" \
    --description "GitHub App credentials for AgentCore GitHub MCP server" \
    --secret-string "$SECRET_VALUE" \
    --region "$AWS_REGION" \
    --output json)
  SECRET_ARN=$(echo "$CREATE_RESPONSE" | jq -r '.ARN')
fi

state_set "github_app_secret_arn" "$SECRET_ARN"
echo "Secret ARN: ${SECRET_ARN}"

echo ""
echo "==> GitHub App secret deployment complete."
echo "    Set GITHUB_APP_SECRET_ARN=${SECRET_ARN} or it will be read from state."
