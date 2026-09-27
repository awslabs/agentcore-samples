#!/usr/bin/env bash
set -euo pipefail

echo "Fetching OpenAI credentials from Secrets Manager..."

if ! SECRET=$(aws secretsmanager get-secret-value \
    --secret-id "openai/codex" \
    --region "${AWS_REGION:-us-west-2}" \
    --query SecretString \
    --output text 2>&1); then
    echo "ERROR: Failed to fetch secret 'openai/codex' from Secrets Manager"
    echo "Please create the secret first:"
    echo "  aws secretsmanager create-secret \\"
    echo "    --name 'openai/codex' \\"
    echo "    --secret-string '{\"api_key\":\"sk-YOUR-KEY\"}' \\"
    echo "    --region ${AWS_REGION:-us-west-2}"
    exit 1
fi

export CODEX_API_KEY=$(echo "$SECRET" | jq -r .api_key)

if [ -z "$CODEX_API_KEY" ] || [ "$CODEX_API_KEY" = "null" ]; then
    echo "ERROR: api_key not found in secret. Expected format: {\"api_key\":\"sk-...\"}"
    exit 1
fi

echo "Credentials loaded. Starting server..."
exec node /app/server.js
