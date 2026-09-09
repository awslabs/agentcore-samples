#!/usr/bin/env bash
#
# Generate frontend/.env from setup/config.json.
# Must be run after all backends + agent are deployed.
#
# Usage:
#   bash setup/5_configure_frontend.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$PROJECT_DIR/setup/config.json"
ENV_FILE="$PROJECT_DIR/frontend/.env"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: $CONFIG_FILE not found. Run deploy_all.sh first."
  exit 1
fi

# Resolve Python
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif [ -f "$PROJECT_DIR/agent/src/.venv/bin/python" ]; then
  PYTHON="$PROJECT_DIR/agent/src/.venv/bin/python"
else
  PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
fi

echo "▸ Generating frontend/.env from config.json..."

$PYTHON -c "
import json

config = json.load(open('$CONFIG_FILE'))
region = config['region']
cognito = config['cognito']
session_api = config.get('session_backend', {}).get('api_url', '')
reviews_api = config.get('reviews_backend', {}).get('api_url', '')
admin_api = config.get('admin_backend', {}).get('api_url', '')
agentcore_url = config.get('agentcore_runtime', {}).get('url', '')

lines = [
    f'VITE_COGNITO_REGION={region}',
    f'VITE_COGNITO_CLIENT_ID={cognito[\"client_id\"]}',
    f'VITE_SESSION_API_URL={session_api}',
    f'VITE_REVIEWS_API_URL={reviews_api}',
    f'VITE_ADMIN_API_URL={admin_api}',
    f'VITE_AGENTCORE_URL={agentcore_url}',
]

with open('$ENV_FILE', 'w') as f:
    f.write('\n'.join(lines) + '\n')

print('  Written to: $ENV_FILE')
for line in lines:
    print(f'    {line[:80]}')
"

echo ""
echo "✅ Frontend configured. Run: cd frontend && npm install && npm run dev"
