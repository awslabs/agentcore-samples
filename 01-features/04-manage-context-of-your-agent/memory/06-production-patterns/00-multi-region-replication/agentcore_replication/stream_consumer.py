"""LTM replication via **record streaming**.

This is the real-time long-term-memory path. The source memory is configured
with ``streamDeliveryResources`` (``MEMORY_RECORDS`` / ``FULL_CONTENT``) so every
extracted/updated record is published to a Kinesis Data Stream. A consumer reads
those stream events and re-creates each record in the target region with
``BatchCreateMemoryRecords``.

Idempotency comes from ``requestIdentifier``: we set it to the source record's
``memoryRecordId``, so replaying the same stream record is a conditional no-op in
the target rather than a duplicate write.

The same core function powers two consumers:

* ``lambda/stream_handler.py`` — production: a Kinesis Event Source Mapping
  invokes the Lambda with a batch of records.
* ``scripts/full_demo.py`` — demo: a local ``GetRecords`` loop feeds the exact
  same logic, so the demo exercises the real stream end-to-end without deploying
  the Lambda.

Stream event shape (decoded Kinesis ``data``)::

    {
      "memoryStreamEvent": {
        "eventType":        "MemoryRecordCreated" | "MemoryRecordUpdated"
                            | "MemoryRecordDeleted" | "StreamingEnabled",
        "memoryId":         "mem-...",
        "memoryRecordId":   "mem-rec-...",
        "memoryRecordText": "the extracted fact text",
        "namespaces":       ["/facts/demo-user", ...],
        "eventTime":        "2026-06-25T12:34:56.789Z",
        "memoryStrategyId": "strat-..."        # when present
      }
    }
"""

import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Stream event types we replicate vs. ignore.
REPLICABLE_EVENTS = {"MemoryRecordCreated", "MemoryRecordUpdated"}
SKIP_EVENTS = {"StreamingEnabled", "MemoryRecordDeleted"}

# Errors worth retrying (let the ESM redeliver / a local loop re-poll); anything
# else is terminal for that record.
RETRYABLE_ERRORS = {"ThrottledException", "ServiceException", "RetryableConflictException"}


def stream_delivery_resources(stream_arn: str) -> dict:
    """Build the ``streamDeliveryResources`` payload for full-content LTM records.

    This is the source-side contract for the record stream this module consumes:
    pass it to ``UpdateMemory``/``CreateMemory`` to publish every extracted record
    (``MEMORY_RECORDS`` / ``FULL_CONTENT``) to the given Kinesis Data Stream.
    """
    return {
        "resources": [
            {
                "kinesis": {
                    "dataStreamArn": stream_arn,
                    "contentConfigurations": [{"type": "MEMORY_RECORDS", "level": "FULL_CONTENT"}],
                }
            }
        ]
    }


@dataclass
class StreamStats:
    """Counters returned by a stream-consumption pass."""

    received: int = 0
    replicated: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "received": self.received,
            "replicated": self.replicated,
            "skipped": self.skipped,
            "failed": self.failed,
            "errors": self.errors,
        }


def _to_epoch(event_time) -> float:
    """Normalize a stream ``eventTime`` (ISO-8601 string) to epoch seconds."""
    if isinstance(event_time, (int, float)):
        return float(event_time)
    if isinstance(event_time, str) and event_time:
        try:
            dt = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp()


def replicate_stream_event(
    stream_event: dict,
    target_client,
    target_memory_id: str,
) -> str:
    """Replicate a single decoded ``memoryStreamEvent`` to the target region.

    Returns ``"replicated"`` or ``"skipped"``, or raises ``ClientError`` on a
    retryable failure so the caller (ESM or local loop) can retry the batch.
    """
    event_type = stream_event.get("eventType", "Unknown")
    record_id = stream_event.get("memoryRecordId", "")

    if event_type in SKIP_EVENTS or event_type not in REPLICABLE_EVENTS:
        # StreamingEnabled is a control event; MemoryRecordDeleted is not
        # replicated (consolidation cleans up the target). Unknown types are
        # ignored forward-compatibly.
        logger.info("skip stream event type=%s id=%s", event_type, record_id)
        return "skipped"

    text = stream_event.get("memoryRecordText")
    if not text:
        logger.warning("skip record %s: no memoryRecordText", record_id)
        return "skipped"

    # Preserve namespaces verbatim so vector search behaves identically in both
    # regions. This is active-passive one-way replication, so no loop-prevention
    # prefix is needed (the target memory does not stream back).
    namespaces = stream_event.get("namespaces") or []
    timestamp = _to_epoch(stream_event.get("eventTime"))

    # Use the source memoryRecordId as the requestIdentifier so replays of the
    # same stream record are idempotent (the target de-dups on requestIdentifier
    # rather than creating a second record). Fall back to a fresh id if the
    # stream event somehow lacks one.
    #
    # NOTE: the source's memoryStrategyId is intentionally NOT forwarded. Strategy
    # IDs are generated per-memory, so the source's ID does not exist in the
    # target and BatchCreateMemoryRecords would reject it. AgentCore associates
    # the replicated record by its namespaces instead.
    record = {
        "requestIdentifier": record_id or uuid.uuid4().hex,
        "content": {"text": text},
        "namespaces": namespaces,
        "timestamp": timestamp,
    }

    resp = target_client.batch_create_memory_records(
        memoryId=target_memory_id,
        records=[record],
        clientToken=uuid.uuid4().hex,
    )

    failed = resp.get("failedRecords", [])
    if failed:
        f = failed[0]
        code = f.get("errorCode", "")
        msg = f"record {record_id}: {code} {f.get('errorMessage')}"
        if code in RETRYABLE_ERRORS:
            # Raise so the ESM/loop retries the whole batch.
            raise ClientError(
                {"Error": {"Code": code, "Message": msg}},
                "BatchCreateMemoryRecords",
            )
        raise RuntimeError(msg)

    logger.info("replicated record %s -> %s", record_id, target_memory_id)
    return "replicated"


def process_kinesis_records(
    kinesis_records,
    target_client,
    target_memory_id: str,
    stats: "StreamStats | None" = None,
) -> StreamStats:
    """Decode and replicate a batch of raw Kinesis records.

    ``kinesis_records`` is a list of dicts each shaped like a Lambda Kinesis
    record (``{"kinesis": {"data": "<base64>"}}``) or a raw ``GetRecords``
    record (``{"Data": b"..."}``). Both shapes are handled.

    A retryable ``ClientError`` is re-raised (so the ESM retries the batch);
    terminal errors are recorded in ``stats`` and skipped.
    """
    stats = stats or StreamStats()
    for rec in kinesis_records:
        stats.received += 1
        try:
            raw = _extract_data(rec)
            payload = json.loads(raw)
            stream_event = payload.get("memoryStreamEvent", payload)
        except Exception as exc:  # noqa: BLE001 - malformed record, never crash
            stats.failed += 1
            stats.errors.append(f"decode: {exc}")
            logger.error("malformed stream record: %s", exc)
            continue

        try:
            outcome = replicate_stream_event(stream_event, target_client, target_memory_id)
            if outcome == "replicated":
                stats.replicated += 1
            else:
                stats.skipped += 1
        except ClientError:
            # Retryable: bubble up so the batch is redelivered.
            raise
        except Exception as exc:  # noqa: BLE001 - terminal for this record
            stats.failed += 1
            stats.errors.append(str(exc))
            logger.error("replicate failed: %s", exc)
    return stats


def _extract_data(rec) -> str:
    """Return the decoded UTF-8 JSON string from a Kinesis record (either shape)."""
    if "kinesis" in rec:  # Lambda ESM event shape
        return base64.b64decode(rec["kinesis"]["data"]).decode("utf-8")
    data = rec.get("Data")  # raw GetRecords shape (boto3 returns bytes)
    if isinstance(data, (bytes, bytearray)):
        return data.decode("utf-8")
    return data


def make_target_client(session, region_name: str):
    """Build a bedrock-agentcore client for the target region."""
    return session.client("bedrock-agentcore", region_name=region_name)
