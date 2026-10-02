#!/bin/bash
# Generate LiveKit token for Playground with AgentCore

read -p "Enter your name: " NAME
read -p "Enter room name (default: playground-test): " ROOM
ROOM=${ROOM:-playground-test}

echo ""
echo "Generating token and invoking AgentCore..."
echo ""

RESPONSE=$(curl -k -s -X POST https://localhost:8443/api/generate-token \
  -H "Content-Type: application/json" \
  -d "{
    \"participant_name\": \"$NAME\",
    \"room_name\": \"$ROOM\",
    \"agent_type\": \"agentcore\"
  }")

TOKEN=$(echo $RESPONSE | jq -r '.token')
SERVER=$(echo $RESPONSE | jq -r '.server_url')

echo "=========================================="
echo "LIVEKIT PLAYGROUND SETUP"
echo "=========================================="
echo ""
echo "1. Go to: https://agents-playground.livekit.io/"
echo ""
echo "2. Enter these details:"
echo "   URL: $SERVER"
echo "   Token: $TOKEN"
echo ""
echo "3. Click 'Connect' and enable microphone"
echo ""
echo "AgentCore agent will join your room shortly!"
echo "=========================================="
