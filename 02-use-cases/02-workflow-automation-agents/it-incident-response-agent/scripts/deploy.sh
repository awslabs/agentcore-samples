#!/usr/bin/env bash
# Deploy the full IT Incident Response Agent
# Usage: ./scripts/deploy.sh
#
# Single-command deployment — `agentcore deploy` handles everything:
#   - Builds the agent container (Docker → ECR)
#   - Creates DynamoDB tables, S3 buckets, Lambda tools, SNS trigger (InfraConstruct)
#   - Creates AgentCore Runtime, Gateway, Memory (AgentCoreApplication + AgentCoreMcp)
#   - Wires Lambda ARNs into Gateway targets automatically
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== IT Incident Response Agent — Deployment ==="
echo ""
echo "This uses the AgentCore CLI to deploy everything in a single stack."
echo "The CDK stack integrates both AgentCore resources AND supplementary infra."
echo ""

# Load .env if present (populates CDK_DEFAULT_ACCOUNT, AWS_REGION, etc.)
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.env"
  set +a
fi

# Validate required env vars
: "${CDK_DEFAULT_ACCOUNT:?ERROR: CDK_DEFAULT_ACCOUNT not set. Copy .env.example to .env and fill in your account ID.}"

# Default region if not set in .env
export AWS_REGION="${AWS_REGION:-us-west-2}"

# Generate aws-targets.json from template (keeps account ID out of git)
TARGETS_TEMPLATE="$PROJECT_DIR/agentcore/aws-targets.json.template"
TARGETS_FILE="$PROJECT_DIR/agentcore/aws-targets.json"
if [ -f "$TARGETS_TEMPLATE" ]; then
  envsubst < "$TARGETS_TEMPLATE" > "$TARGETS_FILE"
  echo "Generated agentcore/aws-targets.json (account: $CDK_DEFAULT_ACCOUNT, region: ${AWS_REGION:-us-west-2})"
fi

# Optional: Pass KB_ID via environment
if [ -n "${KB_ID:-}" ]; then
  echo "Knowledge Base ID: $KB_ID"
  export KB_ID
fi

# Deploy via AgentCore CLI (runs CDK under the hood)
echo ""
echo "Deploying..."
agentcore deploy -y --target dev

echo ""
echo "Deployment complete!"
echo ""
echo "=== Next Steps ==="
echo "  Check status:        agentcore status"
echo "  View logs:           agentcore logs"
echo "  Test locally:        agentcore dev"
echo "  Submit test ticket:  ./scripts/publish_ticket.sh"
