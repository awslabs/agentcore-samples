#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Launch script for AgentCore Identity Streamlit Client
# This script loads environment variables and starts the Streamlit application

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}╔════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  AgentCore Identity - Streamlit Client ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════╝${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check for .env file
if [ -f .env ]; then
    echo -e "${GREEN}✓${NC} Loading environment variables from .env"
    export $(cat .env | grep -v '^#' | xargs)
else
    echo -e "${YELLOW}⚠${NC} No .env file found. Using environment variables."
    echo -e "${YELLOW}⚠${NC} Copy .env.example to .env and configure your settings."
fi

# Also load project root .env for AWS_PROFILE if not set
if [ -z "$AWS_PROFILE" ] && [ -f "../../.env" ]; then
    AWS_PROFILE=$(grep -E "^AWS_PROFILE=" "../../.env" | cut -d'=' -f2)
    if [ -n "$AWS_PROFILE" ]; then
        export AWS_PROFILE
        echo -e "${GREEN}✓${NC} Using AWS_PROFILE: $AWS_PROFILE"
    fi
fi

# Validate required environment variables
REQUIRED_VARS=(
    "AUTH0_DOMAIN"
    "AUTH0_CLIENT_ID"
    "AUTH0_CLIENT_SECRET"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
    echo -e "${RED}✗${NC} Missing required environment variables:"
    for var in "${MISSING_VARS[@]}"; do
        echo -e "  ${RED}-${NC} $var"
    done
    echo ""
    echo "Please set these variables in your .env file or environment."
    exit 1
fi

echo -e "${GREEN}✓${NC} All required environment variables present"
echo ""

# Check if Python dependencies are installed
if ! python3 -c "import streamlit" 2>/dev/null; then
    echo -e "${YELLOW}⚠${NC} Streamlit not found. Installing dependencies..."
    pip install -r requirements.txt
    echo ""
fi

# Display configuration
echo -e "${GREEN}Configuration:${NC}"
echo "  Auth0 Domain: ${AUTH0_DOMAIN}"
echo "  Auth0 Audience: ${AUTH0_AUDIENCE:-https://agentcore-financial-api}"
echo "  Callback URL: ${AUTH0_CALLBACK_URL:-http://localhost:9090/callback}"
echo "  AWS Region: ${AWS_REGION:-us-east-1}"
echo "  Streamlit Port: ${STREAMLIT_PORT:-8501}"
echo "  OAuth Callback Port: ${OAUTH_CALLBACK_PORT:-9090}"
echo ""

# Check if ports are available
if lsof -Pi :${STREAMLIT_PORT:-8501} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${RED}✗${NC} Port ${STREAMLIT_PORT:-8501} is already in use"
    echo "  Please set a different STREAMLIT_PORT in your .env file"
    exit 1
fi

if lsof -Pi :${OAUTH_CALLBACK_PORT:-9090} -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${RED}✗${NC} Port ${OAUTH_CALLBACK_PORT:-9090} is already in use"
    echo "  Please set a different OAUTH_CALLBACK_PORT in your .env file"
    exit 1
fi

echo -e "${GREEN}✓${NC} Ports available"
echo ""

# Start Streamlit
echo -e "${GREEN}Starting Streamlit application...${NC}"
echo ""
echo -e "Access the application at: ${GREEN}http://localhost:${STREAMLIT_PORT:-8501}${NC}"
echo ""
echo "Press Ctrl+C to stop the application"
echo ""

# Run Streamlit with custom configuration
streamlit run app.py \
    --server.port ${STREAMLIT_PORT:-8501} \
    --server.headless true \
    --browser.gatherUsageStats false \
    --server.enableXsrfProtection true \
    --server.enableCORS false
