#!/usr/bin/env bash
# Configure Gateway M2M auth locally BEFORE a fresh deployment. Inbound stays IAM.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
AGENTCORE_CLI="${AGENTCORE_CLI:-agentcore}"
export AGENTCORE_CLI
if [[ "$(python3 "$SCRIPT_DIR/local_cli.py" --version)" != "1.2.0" ]]; then
  echo "ERROR: use AgentCore CLI 1.2.0." >&2
  exit 1
fi

[[ ! -e .sample-deployment.json && ! -e agentcore/.cli ]] || { echo 'Configure auth before fresh deployment; existing state is not adoptable.' >&2; exit 1; }
PROVIDER_NAME="$(python3 -c 'import json; print(json.load(open("agentcore/agentcore.json"))["name"] + "-gateway-m2m")')"
echo "=== Enable CUSTOM_JWT on ITIncidentGateway (Auth0/OIDC M2M) ==="
echo "The CLI stores the secret in gitignored agentcore/.env.local for deploy."
echo "Do not run with shell tracing enabled; protect/remove that file after use."
read -r -p "Auth0 domain (e.g. tenant.us.auth0.com): " AUTH0_DOMAIN
read -r -p "Auth0 M2M Client ID: " AUTH0_CLIENT_ID
read -r -s -p "Auth0 M2M Client Secret: " AUTH0_CLIENT_SECRET
echo
read -r -p "Auth0 API audience: " AUTH0_AUDIENCE
read -r -p "Required M2M scopes (space-separated): " AUTH0_SCOPES
[[ -n "$AUTH0_SCOPES" ]] || { echo 'Explicit scopes are required.' >&2; exit 1; }
read -r -a OAUTH_SCOPES <<< "$AUTH0_SCOPES"
export DISCOVERY_URL="https://${AUTH0_DOMAIN}/.well-known/openid-configuration"
export AUTH0_CLIENT_ID AUTH0_AUDIENCE AUTH0_SCOPES
umask 077
# v1 rejects inline secret values. Pass via stdin, never argv or JSON config.
printf '%s' "$AUTH0_CLIENT_SECRET" | python3 "$SCRIPT_DIR/local_cli.py" add credentials oauth \
  --name "$PROVIDER_NAME" --discovery-url "$DISCOVERY_URL" \
  --client-id "$AUTH0_CLIENT_ID" --scopes "${OAUTH_SCOPES[@]}" --client-secret -
unset AUTH0_CLIENT_SECRET
python3 "$SCRIPT_DIR/configure_gateway_auth.py"

echo "Updated gateway and runtime config; semantic search and LOG_ONLY policy kept."
echo "Ensure .env has no conflicting GATEWAY_AUTH_MODE/OAUTH settings before deploy."
echo 'No deployment performed. Next: scripts/deploy.sh with explicit project/target/account/region.'
echo 'Do not use this helper to update/adopt an existing deployment.'
