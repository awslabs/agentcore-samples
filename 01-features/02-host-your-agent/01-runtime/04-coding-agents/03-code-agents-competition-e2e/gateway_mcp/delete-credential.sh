#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/config.sh"

SECRET_NAME="agentcore/github-mcp/github-app"

echo "==> Deleting GitHub App Secret: ${SECRET_NAME}"

EXISTING=$(aws secretsmanager describe-secret \
  --secret-id "$SECRET_NAME" \
  --region "$AWS_REGION" 2>/dev/null || true)

if [[ -z "$EXISTING" ]]; then
  echo "Secret '${SECRET_NAME}' not found. Nothing to delete."
  exit 0
fi

aws secretsmanager delete-secret \
  --secret-id "$SECRET_NAME" \
  --force-delete-without-recovery \
  --region "$AWS_REGION"

state_set "github_app_secret_arn" ""
echo "==> GitHub App secret deleted."
