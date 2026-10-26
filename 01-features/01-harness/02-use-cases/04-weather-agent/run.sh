#!/bin/bash
#
# Weather Agent — One-Command Runner
#
# Usage:
#   ./run.sh              Full demo (gateway + guardrail + agent + observability + evals)
#   ./run.sh --fast       Skip evals (no 90s wait)
#   ./run.sh --keep       Keep AWS resources after demo (for console inspection)
#   ./run.sh --cleanup    Delete any leftover resources from a previous --keep run
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "============================================================"
echo "  Weather Agent — Harness + Gateway + Guardrails + Evals"
echo "============================================================"
echo -e "${NC}"

# ── Parse flags ──────────────────────────────────────────────────────────────
EXTRA_FLAGS=""
for arg in "$@"; do
    case "$arg" in
        --fast)     EXTRA_FLAGS="$EXTRA_FLAGS --skip-evals" ;;
        --keep)     EXTRA_FLAGS="$EXTRA_FLAGS --skip-cleanup" ;;
        --cleanup)  EXTRA_FLAGS="--cleanup-only" ;;
        --help|-h)
            echo "Usage: ./run.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --fast     Skip evaluations (saves ~90 seconds)"
            echo "  --keep     Keep AWS resources after demo (inspect in console)"
            echo "  --cleanup  Delete leftover resources from a previous --keep run"
            echo "  --help     Show this help"
            echo ""
            echo "Prerequisites:"
            echo "  - AWS CLI configured (aws sts get-caller-identity should work)"
            echo "  - AWS_DEFAULT_REGION set (or defaults to us-east-1)"
            echo "  - Claude Haiku 4.5 model access enabled in Bedrock console"
            echo "  - CloudWatch Transaction Search enabled (for observability)"
            exit 0
            ;;
    esac
done

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
echo -e "${YELLOW}[1/4] Checking prerequisites...${NC}"

# Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}  Python 3 is required but not found.${NC}"
    echo "  Install: https://www.python.org/downloads/"
    exit 1
fi
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "  ${GREEN}Python:${NC} $PYTHON_VERSION"

# AWS CLI
if ! command -v aws &> /dev/null; then
    echo -e "${RED}  AWS CLI is required but not found.${NC}"
    echo "  Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi
echo -e "  ${GREEN}AWS CLI:${NC} $(aws --version 2>&1 | awk '{print $1}')"

# AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}  AWS credentials not configured or expired.${NC}"
    echo "  Run: aws configure"
    exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
echo -e "  ${GREEN}Account:${NC} $ACCOUNT_ID"
echo -e "  ${GREEN}Identity:${NC} ${CALLER_ARN##*/}"

# Region
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$REGION"
echo -e "  ${GREEN}Region:${NC} $REGION"

echo ""

# ── Step 2: Install dependencies ─────────────────────────────────────────────
echo -e "${YELLOW}[2/4] Installing dependencies...${NC}"

HARNESS_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

source "$SCRIPT_DIR/venv/bin/activate"

# Install quietly, only show errors
echo "  Installing Python packages..."
pip install --upgrade pip -q 2>&1 | grep -i error || true
pip install -r "$HARNESS_ROOT/requirements.txt" -q 2>&1 | grep -i error || true
pip install -r "$SCRIPT_DIR/backend/requirements.txt" -q 2>&1 | grep -i error || true

echo -e "  ${GREEN}Dependencies ready${NC}"
echo ""

# ── Step 3: Verify Bedrock model access ──────────────────────────────────────
echo -e "${YELLOW}[3/4] Verifying Bedrock model access...${NC}"

python3 -c "
import boto3, sys
bedrock = boto3.client('bedrock', region_name='$REGION')
try:
    resp = bedrock.get_foundation_model(modelIdentifier='anthropic.claude-haiku-4-5-20251001-v1:0')
    status = resp['modelDetails'].get('modelLifecycle', {}).get('status', 'ACTIVE')
    print(f'  Claude Haiku 4.5: {status}')
except Exception as e:
    if 'AccessDenied' in str(e) or 'ValidationException' in str(e):
        print('  Claude Haiku 4.5: access check inconclusive (may still work via inference profile)')
    else:
        print(f'  Warning: {e}')
" 2>&1

echo ""

# ── Step 4: Run the weather agent ────────────────────────────────────────────
echo -e "${YELLOW}[4/4] Running Weather Agent...${NC}"
echo ""

python3 "$SCRIPT_DIR/weather_agent.py" $EXTRA_FLAGS

deactivate 2>/dev/null || true

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  Done!${NC}"
echo -e "${GREEN}============================================================${NC}"
