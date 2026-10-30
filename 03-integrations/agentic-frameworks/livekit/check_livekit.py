#!/usr/bin/env python3
import os
import sys
import time
from datetime import datetime, timedelta

import jwt
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# LiveKit credentials from environment variables
API_KEY = os.getenv("LIVEKIT_API_KEY")
API_SECRET = os.getenv("LIVEKIT_API_SECRET")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")

if not all([API_KEY, API_SECRET, LIVEKIT_URL]):
    print("Error: Missing LiveKit credentials in environment variables")
    print("Please set LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and LIVEKIT_URL in your .env file")
    sys.exit(1)


# Generate admin token
def generate_admin_token():
    payload = {
        "iss": API_KEY,
        "nbf": int(time.time()),
        "exp": int((datetime.now() + timedelta(hours=1)).timestamp()),
        "video": {"roomList": True},
    }
    return jwt.encode(payload, API_SECRET, algorithm="HS256")


# Convert WebSocket URL to HTTP URL for REST API
http_url = LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")

# List rooms
token = generate_admin_token()
response = requests.post(
    f"{http_url}/twirp/livekit.RoomService/ListRooms",
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    json={},
    timeout=30,
)

print("LiveKit Rooms:")
print(response.text)
