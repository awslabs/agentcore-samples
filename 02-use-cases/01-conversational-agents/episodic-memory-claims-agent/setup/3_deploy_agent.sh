#!/usr/bin/env bash
#
# Deploy the Claims Agent to AgentCore Runtime.
# Injects Cognito config from setup/config.json, deploys, then restores placeholders.
#
# Usage:
#   bash setup/3_deploy_agent.sh [--region us-east-1]
#
set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
if [ "${1:-}" = "--region" ] && [ -n "${2:-}" ]; then
  REGION="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

CONFIG_FILE="setup/config.json"
AGENTCORE_JSON="agent/agentcore/agentcore.json"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: $CONFIG_FILE not found. Run setup/1_deploy_infra.sh first."
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

echo "=== Deploying AgentCore Runtime (region: $REGION) ==="

# Inject Cognito values from config.json
POOL_ID=$($PYTHON -c "import json; print(json.load(open('$CONFIG_FILE'))['cognito']['pool_id'])")
CLIENT_ID=$($PYTHON -c "import json; print(json.load(open('$CONFIG_FILE'))['cognito']['client_id'])")
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"

sed -i '' "s|__COGNITO_DISCOVERY_URL__|${DISCOVERY_URL}|g" "$AGENTCORE_JSON"
sed -i '' "s|__COGNITO_CLIENT_ID__|${CLIENT_ID}|g" "$AGENTCORE_JSON"
echo "  Injected Cognito: pool=$POOL_ID, client=$CLIENT_ID"

# Generate aws-targets.json (account + region for this deployer)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cat > agent/agentcore/aws-targets.json << TARGETS
[
  {
    "name": "default",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
TARGETS
echo "  Generated aws-targets.json: account=$ACCOUNT_ID, region=$REGION"

# Install CDK dependencies if needed
if [ ! -d "agent/agentcore/cdk/node_modules" ]; then
  echo "  Installing CDK dependencies..."
  (cd agent/agentcore/cdk && npm install)
fi

# Deploy
cd agent
agentcore deploy -y -v
cd ..

# Restore placeholders (don't commit real values)
sed -i '' "s|${DISCOVERY_URL}|__COGNITO_DISCOVERY_URL__|g" "$AGENTCORE_JSON"
sed -i '' "s|${CLIENT_ID}|__COGNITO_CLIENT_ID__|g" "$AGENTCORE_JSON"

# Capture runtime URL into config.json
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
RUNTIME_ID=$(aws cloudformation describe-stacks \
  --stack-name AgentCore-ClaimsAgent-default --region "$REGION" \
  --query "Stacks[0].Outputs[?contains(OutputKey,'RuntimeId')].OutputValue" --output text)
RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}"
RUNTIME_URL="https://bedrock-agentcore.${REGION}.amazonaws.com/runtimes/$($PYTHON -c "import urllib.parse; print(urllib.parse.quote('${RUNTIME_ARN}', safe=''))")/invocations"

$PYTHON -c "
import json
config = json.load(open('$CONFIG_FILE'))
config['agentcore_runtime'] = {'url': '${RUNTIME_URL}', 'runtime_id': '${RUNTIME_ID}'}
json.dump(config, open('$CONFIG_FILE', 'w'), indent=2)
"
echo "  Runtime URL written to config.json"
echo ""
echo "✅ Agent deployed: $RUNTIME_ID"
