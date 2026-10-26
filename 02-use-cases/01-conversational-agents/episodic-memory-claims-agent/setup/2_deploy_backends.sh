#!/usr/bin/env bash
#
# Deploy the 3 backend stacks (Session, Reviews, Admin).
#
# Usage:
#   bash setup/2_deploy_backends.sh [--region us-east-1]
#
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
if [ "${1:-}" = "--region" ] && [ -n "${2:-}" ]; then
  REGION="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== Deploying backend stacks (region: $REGION) ==="

echo ""
echo "▸ Session backend..."
bash backend/session/deploy.sh --region "$REGION"

echo ""
echo "▸ Reviews backend..."
bash backend/reviews/deploy.sh --region "$REGION"

echo ""
echo "▸ Admin backend..."
bash backend/admin/deploy.sh --region "$REGION"

echo ""
echo "✅ All backend stacks deployed."
