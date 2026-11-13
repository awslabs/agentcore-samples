#!/bin/bash
set -euo pipefail

echo "=========================================="
echo "AgentCore Private IdP: Keycloak + private CA + VPC Lattice"
echo "=========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."
for cmd in uv cdk aws agentcore npm; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "Error: $cmd is not installed. See README.md > Prerequisites."
        exit 1
    fi
done
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
echo "  Prerequisites met"
echo ""

if [ -f .env ]; then
    echo "Loading configuration from .env..."
    set -a
    source .env
    set +a
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}
export CDK_DEFAULT_ACCOUNT=$ACCOUNT_ID
echo "Account: $ACCOUNT_ID  Region: $AWS_REGION"
echo ""

# Step 1: Infrastructure (VPC, private CA, Keycloak, gateway Lambda + role)
echo "Step 1/4: Deploying infrastructure with CDK (about 20 minutes)..."
uv sync
uv run cdk bootstrap "aws://$ACCOUNT_ID/$AWS_REGION" --qualifier kcidp --toolkit-stack-name CDKToolkit-kcidp
uv run cdk deploy --all --require-approval never
echo ""

# Step 2: AgentCore Identity credential provider + Gateway (privateEndpoint + certificateConfigurations)
echo "Step 2/4: Creating the credential provider and gateway..."
uv run python setup_agentcore.py
echo ""

# Step 3: AgentCore Runtime
echo "Step 3/4: Deploying the agent runtime..."
cd agent/private-idp-keycloak-agent
cat > agentcore/aws-targets.json <<TARGETS
[{"name": "default", "account": "$ACCOUNT_ID", "region": "$AWS_REGION"}]
TARGETS
# The CLI requires this file when credentials are declared. The credential provider is created by
# setup_agentcore.py (the CLI cannot set privateEndpoint/certificateConfigurations), so it stays empty.
touch agentcore/.env.local
(cd agentcore/cdk && npm install --silent)
agentcore deploy -y
echo ""

# Step 4: Test
echo "Step 4/4: Invoking the agent..."
agentcore invoke --prompt "test"
cd ../..

echo ""
echo "=========================================="
echo "Deployment complete. Run ./cleanup_sample.sh to delete the sample resources."
echo "=========================================="
