#!/usr/bin/env bash
#
# Deploy everything from scratch: infrastructure + backends + agent + users.
# Calls each setup script in sequence.
#
# Prerequisites:
#   - AWS credentials configured
#   - Python 3.10+ with .venv set up
#   - Node.js 20+ with @aws/agentcore CLI installed
#   - CDK bootstrapped: npx cdk bootstrap aws://<ACCOUNT>/<REGION>
#
# Usage:
#   bash deploy_all.sh [--region us-east-1]
#
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
if [ "${1:-}" = "--region" ] && [ -n "${2:-}" ]; then
  REGION="$2"
fi

echo "============================================================"
echo "FULL DEPLOYMENT (region: $REGION)"
echo "============================================================"

echo ""
echo "=== Step 1/4: Infrastructure (Cognito + Memory + SSM) ==="
bash setup/1_deploy_infra.sh --region "$REGION"

echo ""
echo "=== Step 2/4: Backend stacks (Session + Reviews + Admin) ==="
bash setup/2_deploy_backends.sh --region "$REGION"

echo ""
echo "=== Step 3/4: AgentCore Runtime ==="
bash setup/3_deploy_agent.sh --region "$REGION"

echo ""
echo "=== Step 4/5: Create demo users ==="
PYTHONPATH=agent/src .venv/bin/python setup/4_create_users.py

echo ""
echo "=== Step 5/5: Configure frontend ==="
bash setup/5_configure_frontend.sh

echo ""
echo "============================================================"
echo "ALL DEPLOYMENTS COMPLETE"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Start frontend:  cd frontend && npm install && npm run dev"
echo "  2. Seed demo data:  bash hydration/full_cycle.sh"
echo "  3. Or pre-demo:     bash hydration/full_cycle_pre_demo.sh"
