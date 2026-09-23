import json
import boto3
import logging
import urllib3
from datetime import datetime
from typing import Dict, Any

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
        response = http.request("PUT", event["ResponseURL"], body=json_response_body, headers=headers)
        logger.info(f"CloudFormation response sent: {response.status}")
    except Exception as e:
        logger.error(f"Failed to send CloudFormation response: {e}")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda function to initialize AgentCore Memory with user preferences
    """
    try:
        logger.info(f"Memory initializer event: {json.dumps(event)}")

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
            timestamp = datetime.utcnow().isoformat() + "Z"

            # Initialize the bedrock-agentcore client
            client = boto3.client("bedrock-agentcore", region_name=region)

            response = client.create_event(
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

            logger.info(f"Successfully created memory event: {response}")

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

    except Exception as e:
        logger.error(f"Error initializing memory: {str(e)}")
        send_response(
            event,
            context,
            "FAILED",
            {},
            event.get("PhysicalResourceId", "memory-init-failed"),
            str(e),
        )
