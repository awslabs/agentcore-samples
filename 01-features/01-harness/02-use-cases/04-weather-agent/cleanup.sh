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
echo ""
echo "Stopping servers..."
[ -f backend.pid ] && kill "$(cat backend.pid)" 2>/dev/null && rm -f backend.pid && echo "  Stopped backend"
[ -f frontend.pid ] && kill "$(cat frontend.pid)" 2>/dev/null && rm -f frontend.pid && echo "  Stopped frontend"
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true

# Delete AWS resources
if [ -f "resource_info.json" ]; then
    echo ""
    echo "Deleting AWS resources..."

    if [ -d "venv" ]; then
        source venv/bin/activate
    else
        echo -e "${RED}  No venv found. Create one first: python3 -m venv venv && source venv/bin/activate && pip install boto3${NC}"
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
