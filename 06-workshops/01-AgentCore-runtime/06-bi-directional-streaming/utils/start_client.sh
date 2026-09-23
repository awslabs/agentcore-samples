#!/bin/bash

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Parse command line arguments
WEBSOCKET_FOLDER=""

usage() {
    echo "Usage: $0 <websocket-folder>"
    echo ""
    echo "Arguments:"
    echo "  websocket-folder    Folder containing the client"
    echo ""
    echo "Example:"
    echo "  ./start_client.sh 01-bedrock-sonic-ws"
    echo "  ./start_client.sh 02-strands-ws"
    echo "  ./start_client.sh 03-langchain-transcribe-polly-ws"
    echo "  ./start_client.sh 04-pipecat-sonic-ws"
    echo ""
    exit 1
}

# Check if folder argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}❌ Error: websocket folder argument is required${NC}"
    echo ""
    usage
fi

WEBSOCKET_FOLDER="$1"

# Resolve the base directory (parent of utils/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Validate folder exists
if [ ! -d "$BASE_DIR/$WEBSOCKET_FOLDER" ]; then
    echo -e "${RED}❌ Error: Folder not found: $BASE_DIR/$WEBSOCKET_FOLDER${NC}"
    echo ""
    echo "Available folders:"
    for dir in 01-bedrock-sonic-ws 02-strands-ws 03-langchain-transcribe-polly-ws 04-pipecat-sonic-ws webrtc-kvs; do
        if [ -d "$BASE_DIR/$dir" ]; then
            echo "  - $dir"
        fi
    done
    echo ""
    exit 1
fi

echo -e "${BLUE}🚀 Starting $WEBSOCKET_FOLDER Client${NC}"
echo ""

# Check for configuration file
CONFIG_FILE="$BASE_DIR/$WEBSOCKET_FOLDER/setup_config.json"

if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}❌ Error: Configuration file not found: $CONFIG_FILE${NC}"
    echo ""
    echo "Please run setup first:"
    echo "  ./setup.sh $WEBSOCKET_FOLDER"
    echo ""
    exit 1
fi

# Check for jq
if ! command -v jq &> /dev/null; then
    echo -e "${RED}❌ Error: jq is not installed${NC}"
    echo "Please install jq to parse JSON configuration"
    exit 1
fi

# Load configuration
echo -e "${YELLOW}📋 Loading configuration from $CONFIG_FILE...${NC}"
AGENT_ARN=$(jq -r '.agent_arn' "$CONFIG_FILE")
AWS_REGION=$(jq -r '.aws_region' "$CONFIG_FILE")

if [ -z "$AGENT_ARN" ] || [ "$AGENT_ARN" = "null" ]; then
    echo -e "${RED}❌ Error: Agent ARN not found in configuration${NC}"
    exit 1
fi

if [ -z "$AWS_REGION" ] || [ "$AWS_REGION" = "null" ]; then
    echo -e "${RED}❌ Error: AWS Region not found in configuration${NC}"
    exit 1
fi

echo -e "${GREEN}✅ Configuration loaded${NC}"
echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "   Folder:       $WEBSOCKET_FOLDER"
echo "   Agent ARN:    $AGENT_ARN"
echo "   AWS Region:   $AWS_REGION"
echo ""

# Export environment variables
export AWS_REGION="$AWS_REGION"

# Check if virtual environment exists (not needed for pipecat — uses npm)
if [ "$WEBSOCKET_FOLDER" != "04-pipecat-sonic-ws" ]; then
    if [ ! -d "$BASE_DIR/venv" ]; then
        echo -e "${YELLOW}⚠️  Virtual environment not found${NC}"
        echo "Creating virtual environment..."
        python3 -m venv "$BASE_DIR/venv"
        source "$BASE_DIR/venv/bin/activate"
        pip install -q -r "$BASE_DIR/$WEBSOCKET_FOLDER/client/requirements.txt"
        echo -e "${GREEN}✅ Virtual environment created${NC}"
    else
        source "$BASE_DIR/venv/bin/activate"
    fi
fi

# Start the client
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🎉 Starting Client${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Different clients have different interfaces
case "$WEBSOCKET_FOLDER" in
    "echo")
        echo -e "${YELLOW}Starting Echo client...${NC}"
        echo ""
        python "$BASE_DIR/$WEBSOCKET_FOLDER/client/client.py" --runtime-arn "$AGENT_ARN"
        ;;
    "04-pipecat-sonic-ws")
        echo -e "${YELLOW}Starting Pipecat client (signing server + Vite)...${NC}"
        echo ""

        cd "$BASE_DIR/$WEBSOCKET_FOLDER/client"
        if [ ! -d "node_modules" ]; then
            echo -e "${YELLOW}Installing npm dependencies...${NC}"
            npm install
        fi

        # Start the signing server in the background (port 8081).
        # The Vite dev server proxies /start to it.
        echo -e "${YELLOW}Starting signing server on port 8081...${NC}"
        python "$BASE_DIR/$WEBSOCKET_FOLDER/client/client.py" \
            --runtime-arn "$AGENT_ARN" \
            --region "$AWS_REGION" &
        SIGNING_PID=$!
        sleep 1

        echo -e "${YELLOW}Starting Vite dev server...${NC}"
        echo -e "${YELLOW}Open the URL shown below in your browser${NC}"
        echo ""

        # Run Vite in foreground; kill signing server on exit
        trap "kill $SIGNING_PID 2>/dev/null" EXIT
        npm run dev
        ;;
    "01-bedrock-sonic-ws"|"02-strands-ws"|"03-langchain-transcribe-polly-ws")
        echo -e "${YELLOW}Starting $WEBSOCKET_FOLDER web client...${NC}"
        echo -e "${YELLOW}The browser will open automatically${NC}"
        echo ""
        python "$BASE_DIR/$WEBSOCKET_FOLDER/client/client.py" --runtime-arn "$AGENT_ARN"
        ;;
    "webrtc-kvs")
        echo -e "${YELLOW}Starting WebRTC KVS client...${NC}"
        echo -e "${YELLOW}The browser will open automatically${NC}"
        echo ""
        python "$BASE_DIR/$WEBSOCKET_FOLDER/client/client.py" --runtime-arn "$AGENT_ARN"
        ;;
    *)
        echo -e "${RED}❌ Error: Unknown folder type: $WEBSOCKET_FOLDER${NC}"
        exit 1
        ;;
esac
