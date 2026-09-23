#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo " AgentCore MCP - Full Teardown"
echo "========================================="
echo ""
echo "This will delete: Gateway → Credential → Runtime → IAM → ECR"
echo ""
read -p "Are you sure? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
"$SCRIPT_DIR/delete-gateway.sh"
echo ""
echo "-----------------------------------------"
echo ""
"$SCRIPT_DIR/delete-credential.sh"
echo ""
echo "-----------------------------------------"
echo ""
"$SCRIPT_DIR/delete-runtime.sh"

echo ""
echo "========================================="
echo " Teardown Complete"
echo "========================================="
