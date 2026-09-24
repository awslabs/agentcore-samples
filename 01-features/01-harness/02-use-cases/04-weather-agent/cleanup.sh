#!/bin/bash
#
# Weather Agent — Cleanup
# Deletes all AWS resources and stops any running servers.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Weather Agent — Cleanup${NC}"
echo "============================================================"

# Stop servers
#
# Validate the pid before killing it. `kill 0` signals the whole process group —
# including this script — so an empty or zeroed pid file (a server that died
# before recording its pid) aborted cleanup right here, at the point where the
# AWS resources are still live and billing. Only ever kill a positive integer.
stop_server() {
    local name="$1" pidfile="$2" pid
    [ -f "$pidfile" ] || return 0
    pid="$(cat "$pidfile" 2>/dev/null)"
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]]; then
        kill "$pid" 2>/dev/null && echo "  Stopped $name"
    fi
    rm -f "$pidfile"
}

echo ""
echo "Stopping servers..."
stop_server backend backend.pid
stop_server frontend frontend.pid
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true

# Delete AWS resources
if [ -f "resource_info.json" ]; then
    echo ""
    echo "Deleting AWS resources..."

    # A missing venv used to abort here with exit 1 — precisely when
    # resource_info.json still names a live, billable harness and gateway, and
    # the message at the end of this script tells you to delete the venv. Fall
    # back to the system interpreter if it already has boto3, and only give up
    # (still non-zero, so the failure is visible) when neither can run.
    if [ -d "venv" ]; then
        source venv/bin/activate
    elif python3 -c "import boto3" 2>/dev/null; then
        echo "  No venv found — using system python3 (boto3 present)"
    else
        echo -e "${RED}  No venv and no system boto3, so the AWS resources in resource_info.json${NC}"
        echo -e "${RED}  cannot be deleted and are still running. Recreate an environment first:${NC}"
        echo -e "${RED}    python3 -m venv venv && source venv/bin/activate && pip install -r backend/requirements.txt${NC}"
        echo -e "${RED}  then re-run ./cleanup.sh${NC}"
        exit 1
    fi

    python3 -c "
import sys
sys.path.insert(0, 'backend')
from resources import destroy_resources
destroy_resources()
"
    deactivate 2>/dev/null || true
else
    echo ""
    echo "  No resource_info.json found — nothing to delete in AWS"
fi

# Clean local artifacts
echo ""
echo "Cleaning local files..."
rm -f backend.log frontend.log backend.pid frontend.pid
echo "  Removed log and pid files"

echo ""
echo -e "${GREEN}Cleanup complete.${NC}"
echo ""
echo "  To run the app again:"
echo "    ./start.sh"
echo ""
echo "  To also remove the virtual environment and node_modules:"
echo "    rm -rf venv frontend/node_modules"
