#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "========================================="
echo " AgentCore MCP - Full Deployment"
echo "========================================="
echo ""

"$SCRIPT_DIR/deploy-credential.sh"
echo ""
echo "-----------------------------------------"
echo ""
"$SCRIPT_DIR/deploy-runtime.sh"
echo ""
echo "-----------------------------------------"
echo ""
"$SCRIPT_DIR/deploy-gateway.sh"

echo ""
echo "========================================="
echo " Deployment Complete"
echo "========================================="
echo ""
source "$SCRIPT_DIR/config.sh"
echo "Gateway URL: $(state_get 'gateway_url')"
echo "Runtime ARN: $(state_get 'runtime_arn')"
echo "State file:  ${STATE_FILE}"
