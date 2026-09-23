#!/usr/bin/env python3
"""
Memory Initialization Script for Weather Agent

This script initializes the AgentCore Memory with activity preferences
that the weather agent uses for recommendations.

Usage:
    python init-memory.py

Environment Variables:
    MEMORY_ID (required): ID of the AgentCore Memory to initialize
    AWS_REGION (required): AWS region where the memory exists
"""

import boto3
import json
import os
import sys
from datetime import datetime


def main():
    """Initialize memory with activity preferences."""

    # Get required environment variables
    memory_id = os.environ.get("MEMORY_ID")
    region = os.environ.get("AWS_REGION")

    if not memory_id:
        print("❌ ERROR: MEMORY_ID environment variable is required")
        sys.exit(1)

    if not region:
        print("❌ ERROR: AWS_REGION environment variable is required")
        sys.exit(1)

    print(f"🎯 Initializing memory: {memory_id}")
    print(f"📍 Region: {region}")

    # Activity preferences data structure
    activity_preferences = {
        "good_weather": [
            "hiking",
            "beach volleyball",
            "outdoor picnic",
            "farmers market",
            "gardening",
            "photography",
            "bird watching",
        ],
        "ok_weather": ["walking tours", "outdoor dining", "park visits", "museums"],
        "poor_weather": ["indoor museums", "shopping", "restaurants", "movies"],
    }

    # Convert to JSON string for storage
    activity_preferences_json = json.dumps(activity_preferences)

    try:
        # Initialize bedrock-agentcore client
        client = boto3.client("bedrock-agentcore", region_name=region)

        # Create timestamp
        timestamp = datetime.utcnow().isoformat() + "Z"

        print("📝 Creating memory event with activity preferences...")

        # Create memory event
        response = client.create_event(
            memoryId=memory_id,
            actorId="user123",
            sessionId="session456",
            eventTimestamp=timestamp,
            payload=[{"blob": activity_preferences_json}],
        )

        print("✅ Memory initialized successfully!")
        print(f"📊 Event ID: {response.get('eventId', 'N/A')}")
        print(f"📦 Preferences stored: {len(activity_preferences)} categories")
        print(f"   - Good weather: {len(activity_preferences['good_weather'])} activities")
        print(f"   - OK weather: {len(activity_preferences['ok_weather'])} activities")
        print(f"   - Poor weather: {len(activity_preferences['poor_weather'])} activities")

        return 0

    except Exception as e:
        print(f"❌ ERROR: Failed to initialize memory: {str(e)}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
