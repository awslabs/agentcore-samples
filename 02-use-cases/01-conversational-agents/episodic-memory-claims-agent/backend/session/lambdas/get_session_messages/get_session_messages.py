import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TABLE_NAME"])

# memory_id is sourced from SSM (single source of truth) so that recreating the
# AgentCore Memory only requires an SSM update — no Lambda redeploy. Cached at
# cold start; falls back to the MEMORY_ID env var if SSM is unavailable.
MEMORY_ID_SSM_PARAM = os.environ.get("MEMORY_ID_SSM_PARAM", "/insurance-claims-demo/memory_id")
_ssm = boto3.client("ssm")


def _resolve_memory_id():
    try:
        return _ssm.get_parameter(Name=MEMORY_ID_SSM_PARAM)["Parameter"]["Value"]
    except (ClientError, BotoCoreError) as e:
        print(f"Could not read {MEMORY_ID_SSM_PARAM} from SSM: {e}; using MEMORY_ID env var")
        return os.environ.get("MEMORY_ID", "")


MEMORY_ID = _resolve_memory_id()


def handler(event, context):
    try:
        claims = event["requestContext"]["authorizer"]["claims"]
        user_id = claims.get("sub")
        session_id = event["pathParameters"]["session_id"]

        # Verify session belongs to user
        session_response = table.get_item(Key={"user_id": user_id, "session_id": session_id})
        if "Item" not in session_response:
            return {
                "statusCode": 404,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                },
                "body": json.dumps({"error": "Session not found"}),
            }

        # actor_id (PH-*) is stored on the session — use it for AgentCore Memory
        session_item = session_response["Item"]
        actor_id = session_item.get("actor_id", user_id)

        print(f"Retrieving messages — memory_id: {MEMORY_ID}, actor_id: {actor_id}, session_id: {session_id}")

        bedrock_agentcore = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )

        response = bedrock_agentcore.list_events(
            memoryId=MEMORY_ID,
            actorId=actor_id,
            sessionId=session_id,
            maxResults=100,
            includePayloads=True,
        )

        events = response.get("events", [])
        print(f"ListEvents returned {len(events)} events")

        messages = []
        for idx, event_item in enumerate(reversed(events)):
            try:
                payload_list = event_item.get("payload", [])
                if not payload_list:
                    content_str = event_item.get("content", "{}")
                    if len(content_str) <= 2:
                        continue
                    content_data = json.loads(content_str)
                    message_data = content_data.get("message", {})
                    role = message_data.get("role", "").lower()
                    content_array = message_data.get("content", [])
                    text_parts = [c["text"] for c in content_array if "text" in c]
                    if text_parts and role in ("user", "assistant"):
                        messages.append(
                            {
                                "role": role,
                                "content": "\n".join(text_parts),
                                "timestamp": message_data.get("created_at"),
                            }
                        )
                    continue

                # New format: payload is a list of payload items
                for payload_item in payload_list:
                    if "conversational" in payload_item:
                        conv = payload_item["conversational"]
                        role = conv.get("role", "").lower()
                        content_obj = conv.get("content", "")

                        if isinstance(content_obj, dict) and "text" in content_obj:
                            content_str = content_obj["text"]
                        elif isinstance(content_obj, str):
                            content_str = content_obj
                        else:
                            continue

                        try:
                            message_data = json.loads(content_str)
                            actual_message = message_data.get("message", {})
                            content_array = actual_message.get("content", [])
                            text_parts = [c["text"] for c in content_array if "text" in c]
                            if text_parts:
                                messages.append(
                                    {
                                        "role": role,
                                        "content": "\n".join(text_parts),
                                        "timestamp": str(event_item.get("eventTimestamp")),
                                    }
                                )
                        except json.JSONDecodeError:
                            if content_str and content_str.startswith("[ADJUSTER DECISION]"):
                                messages.append(
                                    {
                                        "role": "adjuster",
                                        "content": content_str,
                                        "timestamp": str(event_item.get("eventTimestamp")),
                                    }
                                )
                            elif content_str and role in ("user", "assistant"):
                                messages.append(
                                    {
                                        "role": role,
                                        "content": content_str,
                                        "timestamp": str(event_item.get("eventTimestamp")),
                                    }
                                )

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error parsing event {idx}: {e}")
                continue

        print(f"Final: {len(messages)} messages parsed")

        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
                "Access-Control-Allow-Credentials": True,
            },
            "body": json.dumps({"messages": messages, "count": len(messages)}),
        }

    except Exception as e:  # noqa: BLE001 - handler boundary
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            },
            "body": json.dumps({"error": "Could not load conversation history"}),
        }
