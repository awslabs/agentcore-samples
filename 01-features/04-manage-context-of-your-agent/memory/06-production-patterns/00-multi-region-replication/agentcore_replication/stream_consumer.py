"""LTM replication via **record streaming**.

This is the real-time long-term-memory path. The source memory is configured
with ``streamDeliveryResources`` (``MEMORY_RECORDS`` / ``FULL_CONTENT``) so every
extracted/updated record is published to a Kinesis Data Stream. A consumer reads
those stream events and applies each change to the target region: creates with
``BatchCreateMemoryRecords``, updates with ``BatchUpdateMemoryRecords``, and
deletes with ``BatchDeleteMemoryRecords``.

Replays are safe for two reasons. Each create sends a ``clientToken`` derived
from the stream event, so a redelivered event can't create a second record
(``BatchCreateMemoryRecords`` deduplicates on ``clientToken``; ``requestIdentifier``
is for tracking only). And a :class:`~agentcore_replication.record_map.RecordMap`
remembers which target record each source record became, plus the time of the
last change applied, so updates and deletes reach the right record and stale or
redelivered events are skipped.

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
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from .record_map import MappedRecord, RecordMap

logger = logging.getLogger(__name__)

# Stream event types we replicate. Anything else (StreamingEnabled, or a type
# added later) is acknowledged and skipped.
REPLICABLE_EVENTS = {"MemoryRecordCreated", "MemoryRecordUpdated", "MemoryRecordDeleted"}

# ``failedRecords[].errorCode`` is an integer. Throttling (429) and server-side
# errors (5xx) are worth retrying (let the ESM redeliver / a local loop re-poll);
# anything else is terminal for that record.
NOT_FOUND = 404
THROTTLED = 429


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
        # The stream sends nanoseconds; before Python 3.11, fromisoformat accepts at most 6 fractional digits.
        iso = re.sub(r"(\.\d{6})\d+", r"\1", event_time.replace("Z", "+00:00"))
        try:
            dt = datetime.fromisoformat(iso)
            return dt.timestamp()
        except ValueError:
            pass
    return datetime.now(timezone.utc).timestamp()


def _client_token(stream_event: dict) -> str:
    """Derive the create ``clientToken`` from the event, so a redelivered event sends the same token.

    The consumer sends one record per call, so the token depends only on this event.
    """
    key = "|".join(str(stream_event.get(k, "")) for k in ("memoryId", "memoryRecordId", "eventType", "eventTime"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _raise_for_failed(resp: dict, operation: str, record_id: str, ignore_not_found: bool = False) -> None:
    """Raise on ``failedRecords``: ``ClientError`` if retryable (so the batch is retried), else ``RuntimeError``."""
    for failed in resp.get("failedRecords", []):
        code = failed.get("errorCode") or 0
        if ignore_not_found and code == NOT_FOUND:
            continue
        msg = f"record {record_id}: {code} {failed.get('errorMessage')}"
        if code == THROTTLED or code >= 500:
            raise ClientError({"Error": {"Code": str(code), "Message": msg}}, operation)
        raise RuntimeError(msg)


def replicate_stream_event(
    stream_event: dict,
    target_client,
    target_memory_id: str,
    record_map: RecordMap,
) -> str:
    """Apply a single decoded ``memoryStreamEvent`` to the target region.

    Returns ``"replicated"`` or ``"skipped"``, or raises ``ClientError`` on a
    retryable failure so the caller (ESM or local loop) can retry the batch.
    """
    event_type = stream_event.get("eventType", "Unknown")
    record_id = stream_event.get("memoryRecordId", "")

    if event_type not in REPLICABLE_EVENTS or not record_id:
        # StreamingEnabled is a control event; unknown types are ignored forward-compatibly.
        logger.info("skip stream event type=%s id=%s", event_type, record_id)
        return "skipped"

    event_time = _to_epoch(stream_event.get("eventTime"))
    mapped = record_map.get(record_id)
    if mapped and (event_time <= mapped.event_time or mapped.deleted):
        # Redelivered or out of order: this change, or a newer one, was already applied.
        logger.info("skip stale %s for %s", event_type, record_id)
        return "skipped"

    if event_type == "MemoryRecordDeleted":
        # Delete events carry only IDs, so the map supplies the target record.
        outcome = "skipped"
        if mapped and mapped.target_record_id:
            resp = target_client.batch_delete_memory_records(
                memoryId=target_memory_id,
                records=[{"memoryRecordId": mapped.target_record_id}],
            )
            _raise_for_failed(resp, "BatchDeleteMemoryRecords", record_id, ignore_not_found=True)
            outcome = "replicated"
        # Keep a tombstone so a late or redelivered create for this record is skipped.
        target_record_id = mapped.target_record_id if mapped else None
        record_map.put(record_id, MappedRecord(target_record_id, event_time, deleted=True))
        logger.info("deleted record %s from %s", record_id, target_memory_id)
        return outcome

    text = stream_event.get("memoryRecordText")
    if not text:
        logger.warning("skip record %s: no memoryRecordText", record_id)
        return "skipped"

    # Preserve namespaces verbatim so vector search behaves identically in both
    # regions. This is active-passive one-way replication, so no loop-prevention
    # prefix is needed (the target memory does not stream back).
    #
    # NOTE: the source's memoryStrategyId is intentionally NOT forwarded. Strategy
    # IDs are generated per-memory, so the source's ID does not exist in the
    # target and the Batch APIs would reject it. AgentCore associates the
    # replicated record by its namespaces instead.
    namespaces = stream_event.get("namespaces") or []

    if mapped:
        # Already replicated: apply the newer content to the same target record.
        resp = target_client.batch_update_memory_records(
            memoryId=target_memory_id,
            records=[
                {
                    "memoryRecordId": mapped.target_record_id,
                    "timestamp": event_time,
                    "content": {"text": text},
                    "namespaces": namespaces,
                }
            ],
        )
        _raise_for_failed(resp, "BatchUpdateMemoryRecords", record_id)
        record_map.put(record_id, MappedRecord(mapped.target_record_id, event_time))
        logger.info("updated record %s in %s", record_id, target_memory_id)
        return "replicated"

    # Not replicated yet (a create, or an update whose create predates streaming).
    resp = target_client.batch_create_memory_records(
        memoryId=target_memory_id,
        records=[
            {
                "requestIdentifier": record_id,
                "content": {"text": text},
                "namespaces": namespaces,
                "timestamp": event_time,
            }
        ],
        clientToken=_client_token(stream_event),
    )
    _raise_for_failed(resp, "BatchCreateMemoryRecords", record_id)
    created = resp.get("successfulRecords", [])
    if not created:
        raise RuntimeError(f"record {record_id}: BatchCreateMemoryRecords returned no record ID")
    record_map.put(record_id, MappedRecord(created[0]["memoryRecordId"], event_time))
    logger.info("replicated record %s -> %s", record_id, target_memory_id)
    return "replicated"


def process_kinesis_records(
    kinesis_records,
    target_client,
    target_memory_id: str,
    record_map: RecordMap,
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
            outcome = replicate_stream_event(stream_event, target_client, target_memory_id, record_map)
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
