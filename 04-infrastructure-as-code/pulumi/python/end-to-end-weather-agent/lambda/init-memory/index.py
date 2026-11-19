import json
import logging
from datetime import datetime, timezone

import boto3


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)


def handler(event, _context):
    LOGGER.info("Received event: %s", json.dumps(event))

    memory_id = event["memoryId"]
    region = event.get("region")

    client = boto3.client("bedrock-agentcore", region_name=region)

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

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    response = client.create_event(
        memoryId=memory_id,
        actorId="user123",
        sessionId="session456",
        eventTimestamp=timestamp,
        payload=[{"blob": json.dumps(activity_preferences)}],
    )

    event_id = response.get("eventId", "unknown")
    LOGGER.info("Memory initialized with event %s", event_id)

    return {
        "memoryId": memory_id,
        "eventId": event_id,
        "status": "initialized",
    }
