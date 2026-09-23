#!/bin/bash
set -e

echo "=========================================="
echo "AWS Samples: ECS Agent with OAuth Session Binding"
echo "=========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v uv &> /dev/null; then
    echo "❌ Error: uv is not installed"
    echo "   Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "✓ uv installed"

if ! command -v docker &> /dev/null; then
    echo "❌ Error: docker is not installed"
    echo "   Install: https://docs.docker.com/get-docker/"
    exit 1
fi
echo "✓ docker installed"

if ! command -v cdk &> /dev/null; then
    echo "❌ Error: AWS CDK is not installed"
    echo "   Install: npm install -g aws-cdk"
    exit 1
fi
echo "✓ AWS CDK installed"

if ! command -v aws &> /dev/null; then
    echo "❌ Error: AWS CLI is not installed"
    echo "   Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Check AWS CLI version (bedrock-agentcore requires v2.27+)
AWS_CLI_VERSION=$(aws --version 2>&1 | sed -n 's/.*aws-cli\/\([0-9]*\.[0-9]*\).*/\1/p')
MIN_VERSION="2.27"
if [ "$(printf '%s\n' "$MIN_VERSION" "$AWS_CLI_VERSION" | sort -V | head -n1)" != "$MIN_VERSION" ]; then
    echo "❌ Error: AWS CLI version $AWS_CLI_VERSION is too old (requires >= $MIN_VERSION)"
    echo "   Update: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi
echo "✓ AWS CLI installed (v$AWS_CLI_VERSION)"

echo ""
echo "All prerequisites met!"
echo ""

# Activate virtual environment
echo "Activating Python virtual environment..."
if [ ! -d ".venv" ]; then
    echo "❌ Error: virtual environment is not installed"
    echo "   Install: https://docs.astral.sh/uv/pip/environments/#creating-a-virtual-environment"
    exit 1
fi
source .venv/bin/activate || { echo "✗ Failed to activate virtual environment"; exit 1; }
echo "✓ Virtual environment activated"
echo ""

# Export dependencies
echo "Exporting dependencies with uv..."

echo "  Exporting backend/runtime/requirements.txt..."
uv export --format requirements-txt --only-group runtime --no-hashes > backend/runtime/requirements.txt

echo "  Exporting backend/session_binding/requirements.txt..."
uv export --format requirements-txt --only-group oauth --no-hashes > backend/session_binding/requirements.txt

echo "✓ Dependencies exported"
echo ""

# Deploy with CDK
echo "Deploying with CDK..."
uv run cdk bootstrap --qualifier sample3lo --toolkit-stack-name CDKToolkit-sample3lo

uv run cdk synth --quiet && uv run checkov -d cdk.out --framework cloudformation dockerfile --compact --quiet

uv run cdk deploy --all --require-approval never

echo ""
echo "=========================================="
echo "✓ Deployment complete!"
echo "=========================================="

