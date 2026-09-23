"""AgentCore Memory cross-region replication consumer.

Consumes memory record stream events from Kinesis and replicates
them to a remote region's AgentCore Memory instance.

Loop prevention: replicated records use a 'replicated/' namespace prefix.
The consumer skips events where any namespace starts with 'replicated/'.
"""

import base64
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REMOTE_REGION = os.environ["REMOTE_REGION"]
REMOTE_MEMORY_ID = os.environ["REMOTE_MEMORY_ID"]
LOCAL_REGION = os.environ["LOCAL_REGION"]
DLQ_URL = os.environ["DLQ_URL"]

REPLICATED_PREFIX = "replicated/"

remote_client = boto3.client("bedrock-agentcore", region_name=REMOTE_REGION)
sqs_client = boto3.client("sqs")

REPLICABLE_EVENTS = {"MemoryRecordCreated", "MemoryRecordUpdated"}
SKIP_EVENTS = {"StreamingEnabled", "MemoryRecordDeleted"}
RETRYABLE_ERRORS = {"ThrottledException", "ServiceException"}


def lambda_handler(event, context):
    """Process a batch of Kinesis records containing memory stream events."""
    for record in event["Records"]:
        try:
            payload = json.loads(base64.b64decode(record["kinesis"]["data"]).decode("utf-8"))
        except Exception as e:
            logger.error(
                json.dumps(
                    {
                        "action": "malformed_record",
                        "error": str(e),
                        "kinesis_sequence": record.get("kinesis", {}).get("sequenceNumber", "N/A"),
                    }
                )
            )
            _safe_dlq_send({"raw_record": str(record)}, "malformed", str(e))
            continue

        stream_event = payload.get("memoryStreamEvent", {})
        event_type = stream_event.get("eventType", "Unknown")
        record_id = stream_event.get("memoryRecordId", "N/A")

        logger.info(
            json.dumps(
                {
                    "action": "received",
                    "event_type": event_type,
                    "memory_record_id": record_id,
                }
            )
        )

        if event_type in SKIP_EVENTS:
            continue

        if event_type not in REPLICABLE_EVENTS:
            logger.warning(
                json.dumps(
                    {
                        "action": "skipped",
                        "event_type": event_type,
                        "reason": "unknown event type",
                    }
                )
            )
            continue

        if _is_replicated(stream_event):
            logger.info(
                json.dumps(
                    {
                        "action": "skipped",
                        "memory_record_id": record_id,
                        "reason": "replicated prefix",
                    }
                )
            )
            continue

        memory_record_text = stream_event.get("memoryRecordText")
        if not memory_record_text:
            _safe_dlq_send(stream_event, "missing_content", "memoryRecordText absent")
            continue

        try:
            _replicate(stream_event)
            logger.info(json.dumps({"action": "replicated", "memory_record_id": record_id}))
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in RETRYABLE_ERRORS:
                raise
            else:
                _safe_dlq_send(stream_event, "non_retryable", str(e))


def _is_replicated(stream_event):
    namespaces = stream_event.get("namespaces") or []
    return any(ns.startswith(REPLICATED_PREFIX) for ns in namespaces)


def _replicate(stream_event):
    original_namespaces = stream_event.get("namespaces") or []
    replicated_namespaces = [f"{REPLICATED_PREFIX}{ns}" for ns in original_namespaces[:1]] or [
        REPLICATED_PREFIX.rstrip("/")
    ]

    original_id = stream_event.get("memoryRecordId", "")
    event_time = stream_event.get("eventTime", "")
    request_id = hashlib.sha256(f"{LOCAL_REGION}:{original_id}:{event_time}".encode()).hexdigest()[:36]

    try:
        dt = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        timestamp = int(dt.timestamp())
    except (ValueError, AttributeError):
        timestamp = int(datetime.now(timezone.utc).timestamp())

    response = remote_client.batch_create_memory_records(
        memoryId=REMOTE_MEMORY_ID,
        records=[
            {
                "requestIdentifier": request_id,
                "content": {"text": stream_event["memoryRecordText"]},
                "namespaces": replicated_namespaces,
                "timestamp": timestamp,
            }
        ],
    )

    failed = response.get("failedRecords", [])
    if failed:
        raise ClientError(
            {
                "Error": {
                    "Code": "BatchPartialFailure",
                    "Message": failed[0].get("errorMessage", "unknown"),
                }
            },
            "BatchCreateMemoryRecords",
        )


def _safe_dlq_send(stream_event, error_type, error_message):
    record_id = stream_event.get("memoryRecordId", "N/A")
    logger.error(
        json.dumps(
            {
                "action": "dlq",
                "memory_record_id": record_id,
                "error_type": error_type,
                "error_message": error_message,
            }
        )
    )
    try:
        sqs_client.send_message(
            QueueUrl=DLQ_URL,
            MessageBody=json.dumps(
                {
                    "source_region": LOCAL_REGION,
                    "event_type": stream_event.get("eventType", "Unknown"),
                    "memory_id": stream_event.get("memoryId", ""),
                    "memory_record_id": record_id,
                    "error_type": error_type,
                    "error_message": error_message,
                    "original_event": stream_event,
                }
            ),
        )
    except Exception as dlq_err:
        logger.error(
            json.dumps(
                {
                    "action": "dlq_write_failed",
                    "memory_record_id": record_id,
                    "dlq_error": str(dlq_err),
                }
            )
        )
