#!/bin/bash
set -euo pipefail

# ============================================================================
# Event-Driven Claims Agent — Full Teardown
#
# Usage: ./scripts/destroy.sh [region]
#
# Destroys everything created by deploy.sh in one shot:
# - CloudWatch observability deliveries
# - CloudFormation stack (with DELETE_FAILED auto-recovery)
# - Orphaned AgentCore control-plane resources (Gateway, Runtime, Memory, etc.)
# - Cognito User Pool (if created by setup_cognito.sh)
# - Local state files
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REGION="${1:-${AWS_REGION:-us-west-2}}"

echo "🗑️  Tearing down Claims Agent in $REGION..."
echo ""

# Step 1: Observability (non-critical — skip if already gone)
echo "🔭 Step 1: Removing observability deliveries..."
python3 "$SCRIPT_DIR/disable_observability.py" --region "$REGION" \
  --stack-name "AgentCore-ClaimsAgent-dev" 2>/dev/null || \
  echo "   Skipped (already removed or never enabled)"
echo ""

# Step 2: Stack + orphans + Cognito + local state (all handled by one script)
echo "💥 Step 2: Destroying stack and cleaning up resources..."
python3 "$SCRIPT_DIR/cleanup_agentcore.py" --region "$REGION" --project-dir "$PROJECT_DIR"
echo ""

echo "✅ Teardown complete!"
echo "   All AWS resources destroyed. Local .env preserved (delete manually if desired)."
