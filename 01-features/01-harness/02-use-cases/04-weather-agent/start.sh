#!/bin/bash
#
# Weather Agent — One-Command Start
# Sets up everything and starts the web app.
#
# Usage:
#   ./start.sh          Start the full app (provisions AWS resources on first run)
#   ./start.sh --help   Show options
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}"
echo "=================================================================================="
echo "  Weather Agent — Harness + Gateway + Guardrails + Skills + Evals + Optimization"
echo "=================================================================================="
echo -e "${NC}"

# ── Cleanup on exit ──────────────────────────────────────────────────────────
cleanup() {
    echo ""
    echo -e "${YELLOW}Stopping servers...${NC}"
    [ -f backend.pid ] && kill "$(cat backend.pid)" 2>/dev/null && rm -f backend.pid
    [ -f frontend.pid ] && kill "$(cat frontend.pid)" 2>/dev/null && rm -f frontend.pid
    lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
    lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true
    echo -e "${GREEN}Stopped.${NC}"
    echo ""
    echo "  To resume the app:            ./start.sh  (reuses existing AWS resources)"
    echo "  To delete AWS resources:       ./cleanup.sh"
    exit 0
}
trap cleanup SIGINT SIGTERM

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
echo -e "${YELLOW}[1/5] Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}  Python 3 is required. Install: https://www.python.org/downloads/${NC}"
    exit 1
fi
echo -e "  ${GREEN}Python:${NC} $(python3 --version 2>&1 | awk '{print $2}')"

if ! command -v node &> /dev/null; then
    echo -e "${RED}  Node.js is required. Install: https://nodejs.org/${NC}"
    exit 1
fi
echo -e "  ${GREEN}Node.js:${NC} $(node --version)"

if ! command -v aws &> /dev/null; then
    echo -e "${RED}  AWS CLI is required.${NC}"
    exit 1
fi

if ! aws sts get-caller-identity &> /dev/null; then
    echo -e "${RED}  AWS credentials not configured or expired.${NC}"
    exit 1
fi
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
echo -e "  ${GREEN}AWS Account:${NC} $ACCOUNT_ID"
echo -e "  ${GREEN}Identity:${NC} ${CALLER_ARN##*/}"

REGION="${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo 'us-east-1')}"
export AWS_DEFAULT_REGION="$REGION"
echo -e "  ${GREEN}Region:${NC} $REGION"
echo ""

# ── Step 2: Python virtual environment + deps ────────────────────────────────
echo -e "${YELLOW}[2/5] Setting up Python environment...${NC}"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created virtual environment"
fi

source venv/bin/activate
pip install --upgrade pip -q 2>&1 | grep -i error || true
pip install --upgrade -r backend/requirements.txt -q 2>&1 | grep -i error || true
BOTO_VERSION=$(python3 -c "import boto3; print(boto3.__version__)" 2>/dev/null)
echo -e "  ${GREEN}Python dependencies ready${NC} (boto3: $BOTO_VERSION)"
echo ""

# ── Step 3: Frontend dependencies ────────────────────────────────────────────
echo -e "${YELLOW}[3/5] Setting up frontend...${NC}"

if [ ! -d "frontend/node_modules" ]; then
    cd frontend
    npm install --silent 2>&1 | tail -1
    cd ..
    echo "  Installed Node.js packages"
else
    echo "  Node.js packages already installed"
fi
echo -e "  ${GREEN}Frontend ready${NC}"
echo ""

# ── Step 4: Start backend ────────────────────────────────────────────────────
echo -e "${YELLOW}[4/5] Starting backend (provisions AWS resources on first run)...${NC}"

lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

(
    cd backend
    python3 main.py 2>&1 | tee ../backend.log &
    echo $! > ../backend.pid
)

# Wait for backend to be ready
echo "  Waiting for backend (this includes AWS resource provisioning, may take 3-5 minutes)..."
MAX_WAIT=360
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "  ${GREEN}Backend ready${NC}"
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
    if [ $((ELAPSED % 15)) -eq 0 ]; then
        echo "  Still provisioning... (${ELAPSED}s)"
    fi
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo -e "${RED}  Backend failed to start. Check backend.log${NC}"
    exit 1
fi
echo ""

# ── Step 5: Start frontend ───────────────────────────────────────────────────
echo -e "${YELLOW}[5/5] Starting frontend...${NC}"

lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

(
    cd frontend
    npm run dev 2>&1 | tee ../frontend.log &
    echo $! > ../frontend.pid
)

sleep 3
echo -e "  ${GREEN}Frontend ready${NC}"

echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  App is running!${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "  ${BLUE}Open:${NC}  http://localhost:5173"
echo ""
echo "  Logs:"
echo "    Backend:   tail -f backend.log"
echo "    Frontend:  tail -f frontend.log"
echo ""
echo "  Press Ctrl+C to stop servers"
echo "  Run ./cleanup.sh to delete AWS resources"
echo ""

# Wait
while true; do sleep 1; done
