import json
import logging
from datetime import UTC, datetime
from typing import Any

import boto3
import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def send_response(
    event,
    context,
    response_status,
    response_data=None,
    physical_resource_id=None,
    reason=None,
):
    """Send response to CloudFormation"""
    response_data = response_data or {}
    physical_resource_id = physical_resource_id or context.log_stream_name
    reason = reason or f"See CloudWatch Log Stream: {context.log_stream_name}"

    response_body = {
        "Status": response_status,
        "Reason": reason,
        "PhysicalResourceId": physical_resource_id,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": response_data,
    }

    json_response_body = json.dumps(response_body)

    headers = {"content-type": "", "content-length": str(len(json_response_body))}

    try:
        http = urllib3.PoolManager()
        response = http.request(
            "PUT",
            event["ResponseURL"],
            body=json_response_body,
            headers=headers,
            redirect=False,
            retries=False,
            timeout=10,
        )
    except urllib3.exceptions.HTTPError:
        raise RuntimeError("CloudFormation response delivery failed") from None
    if not 200 <= response.status < 300:
        raise RuntimeError(f"CloudFormation response delivery returned HTTP {response.status}")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda function to initialize AgentCore Memory with user preferences
    """
    try:
        logger.info("Memory initializer request: %s", event["RequestType"])

        request_type = event.get("RequestType")
        memory_id = event["ResourceProperties"]["MemoryId"]
        region = event["ResourceProperties"]["Region"]

        if request_type == "Create":
            logger.info(f"Initializing memory {memory_id} in region {region}")

            # Activity preferences matching the working CloudFormation template
            activity_preferences = {
                "good_weather": [
                    "hiking",
                    "beach volleyball",
                    "outdoor picnic",
                    "cycling",
                    "rock climbing",
                ],
                "ok_weather": [
                    "walking tours",
                    "outdoor dining",
                    "park visits",
                    "sightseeing",
                    "farmers markets",
                ],
                "poor_weather": [
                    "indoor museums",
                    "shopping",
                    "restaurants",
                    "movie theaters",
                    "art galleries",
                ],
            }

            # Convert to JSON string for storage in blob
            activity_preferences_json = json.dumps(activity_preferences)

            # Get current timestamp
            timestamp = datetime.now(UTC)

            # Initialize the bedrock-agentcore client
            client = boto3.client("bedrock-agentcore", region_name=region)

            client.create_event(
                memoryId=memory_id,
                actorId="user123",
                sessionId="session456",
                eventTimestamp=timestamp,
                payload=[
                    {
                        "blob": activity_preferences_json,
                    }
                ],
            )

            logger.info("Created sample preferences event")

            send_response(
                event,
                context,
                "SUCCESS",
                {"MemoryId": memory_id, "Status": "INITIALIZED"},
                f"memory-init-{memory_id}",
            )

        elif request_type in ["Update", "Delete"]:
            logger.info(f"Handling {request_type} request - no action needed")
            send_response(
                event,
                context,
                "SUCCESS",
                {},
                event.get("PhysicalResourceId", f"memory-init-{memory_id}"),
            )

    except Exception as e:  # noqa: BLE001 - Return a fixed CloudFormation failure without leaking request details.
        logger.error("Memory initializer failed (%s)", type(e).__name__)
        send_response(
            event,
            context,
            "FAILED",
            {},
            event.get("PhysicalResourceId", "memory-init-failed"),
            "Memory initializer failed",
        )
