"""Production patterns for AgentCore Memory — reference, not a demo.

This module is meant to be *read and copy-pasted*, not run. It deliberately
has no conversation, no `__main__` flow, and no live API calls. Each function
is a self-contained pattern you can lift into your own agent:

    - _is_retryable / RETRYABLE_DATAPLANE  : classify errors correctly
    - call_with_backoff                    : exponential backoff WITH JITTER
    - recall_with_degradation              : memory failures never break a turn
    - record_turn_durably                  : writes buffer-and-retry, not drop
    - batch_create_with_partial_handling   : inspect failedRecords, chunk at 100
    - MemoryResource (context manager)      : try/finally cleanup that can't leak
    - health_check                          : cheap readiness probe

Every constant and error name below is grounded in the AgentCore /
AgentCore-Control API references and the `bedrock_agentcore` SDK source.
See ./01-error-handling.md for the reasoning behind each retry decision.

Prerequisites (if you adapt this into something runnable):
    pip install boto3 bedrock-agentcore
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Any, Callable, Iterator, Optional

from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger("agentcore.memory.production")

# --- Fixed service constraints (from the API references) ------------------
# Safe to rely on; these are modeled field limits, not adjustable quotas.
MAX_RECORDS_PER_BATCH = 100  # BatchCreate/Update/DeleteMemoryRecords `records`
MAX_PAYLOAD_ITEMS_PER_EVENT = 100  # CreateEvent `payload`
MAX_EVENT_METADATA_ENTRIES = 15  # CreateEvent `metadata`
MAX_METADATA_FILTERS = 5  # RetrieveMemoryRecords (SDK-enforced)
MIN_EVENT_EXPIRY_DAYS = 3  # CreateMemory `eventExpiryDuration`
MAX_EVENT_EXPIRY_DAYS = 365  # CreateMemory `eventExpiryDuration`

# --- Retry classification -------------------------------------------------
# ONLY these transient data-plane errors are worth retrying. Everything else
# (ValidationException, InvalidInputException, ResourceNotFoundException,
# AccessDeniedException, ServiceQuotaExceededException) is deterministic:
# the same request fails the same way, so retrying just burns latency.
#
# Note: `RetryableConflictException` is raised by CreateEvent only.
# The control plane raises `ConflictException` instead, which is NOT
# retry-able (e.g. duplicate name) — so it is intentionally absent here.
RETRYABLE_DATAPLANE = frozenset(
    {
        "ThrottledException",  # 429 — rate exceeded; back off
        "RetryableConflictException",  # 409 — transient write conflict (CreateEvent)
        "ServiceException",  # 500 — internal/transient
    }
)


def _is_retryable(error: ClientError) -> bool:
    """Return True only for transient errors that a retry might fix."""
    return error.response["Error"]["Code"] in RETRYABLE_DATAPLANE


# --- Pattern 1: exponential backoff with full jitter ----------------------
def call_with_backoff(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 5,
    base: float = 0.5,
    cap: float = 20.0,
    **kwargs: Any,
) -> Any:
    """Invoke `fn(*args, **kwargs)`, retrying only transient errors.

    Uses FULL JITTER: delay = uniform(0, min(cap, base * 2**attempt)).
    Full jitter prevents retry storms where every throttled client retries
    in lockstep and re-throttles the service. A fixed or non-jittered delay
    is the classic way to turn a brief throttle into an outage.

    Deterministic errors (validation, not-found, access-denied, quota) are
    re-raised immediately — retrying them is pure waste. The final attempt
    always re-raises so the caller can decide how to degrade.

    This is an application-level layer ON TOP OF botocore's own retries
    (see configured_clients below); it lets you decide retry-ability per
    modeled exception and emit your own per-retry metrics.
    """
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if not _is_retryable(e) or attempt == max_attempts - 1:
                # Deterministic failure, or we're out of attempts. Surface it.
                raise
            delay = random.uniform(0, min(cap, base * (2**attempt)))
            logger.warning(
                "transient error %s on %s (attempt %d/%d); retrying in %.2fs",
                code,
                getattr(fn, "__name__", "call"),
                attempt + 1,
                max_attempts,
                delay,
            )
            time.sleep(delay)


def configured_clients(region: str) -> tuple[Any, Any]:
    """Build data- and control-plane clients with a sensible retry config.

    botocore's `standard` mode already retries throttling/5xx ~3 times with
    backoff; `adaptive` adds client-side rate limiting that pre-emptively
    slows down when it sees throttling. We raise max_attempts for bulk paths.

    NOTE: the `bedrock_agentcore` MemoryClient builds its own clients and does
    NOT raise these defaults — if you use the SDK and want this behaviour,
    keep the application-level `call_with_backoff` above as your retry layer.
    """
    import boto3

    cfg = Config(retries={"max_attempts": 5, "mode": "adaptive"})
    data = boto3.client("bedrock-agentcore", region_name=region, config=cfg)
    control = boto3.client("bedrock-agentcore-control", region_name=region, config=cfg)
    return data, control


# --- Pattern 2: graceful degradation on reads ----------------------------
def recall_with_degradation(
    data_client: Any,
    memory_id: str,
    namespace: str,
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """Retrieve memories, but NEVER let a memory failure break the turn.

    Memory is an enhancement, not a hard dependency on the response path.
    If retrieval throttles or the service is briefly down, we log, emit a
    metric (placeholder), and return [] so the agent still answers — just
    without remembered context.
    """
    try:
        resp = call_with_backoff(
            data_client.retrieve_memory_records,
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": top_k},
        )
        return resp.get("memoryRecordSummaries", [])
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # Degrade gracefully: the user gets a less personalized answer,
        # not an error. Emit a metric so the degradation is visible.
        logger.warning("recall failed (%s); continuing without memory context", code)
        _emit_metric("MemoryRecallDegraded", 1)
        return []


# --- Pattern 3: durable writes (buffer-and-retry, don't drop) -------------
def record_turn_durably(
    data_client: Any,
    memory_id: str,
    actor_id: str,
    session_id: str,
    messages: list[tuple[str, str]],
    dead_letter: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Persist a conversation turn. Returns True on success.

    Writes are asymmetric to reads: silently dropping a write LOSES history.
    So we retry transient errors, and on final failure we hand the payload to
    a dead-letter sink (a queue, a file, a DynamoDB row) for later replay
    rather than discarding it. Only give up after that safety net.
    """
    if len(messages) > MAX_PAYLOAD_ITEMS_PER_EVENT:
        raise ValueError(
            f"payload has {len(messages)} items; CreateEvent allows "
            f"{MAX_PAYLOAD_ITEMS_PER_EVENT}. Split into multiple events."
        )
    payload = [{"conversational": {"role": role, "content": {"text": text}}} for text, role in messages]
    try:
        call_with_backoff(
            data_client.create_event,
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            payload=payload,
            clientToken=str(uuid.uuid4()),  # idempotent retries
        )
        return True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        logger.error("create_event failed permanently (%s); dead-lettering", code)
        _emit_metric("MemoryWriteDeadLettered", 1)
        if dead_letter is not None:
            dead_letter(
                {
                    "memoryId": memory_id,
                    "actorId": actor_id,
                    "sessionId": session_id,
                    "messages": messages,
                }
            )
        return False


# --- Pattern 4: batch writes with partial-failure handling ----------------
def _chunked(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def batch_create_with_partial_handling(
    data_client: Any,
    memory_id: str,
    records: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Create records in chunks of 100, inspecting per-record outcomes.

    Batch calls return HTTP 200/201 EVEN WHEN individual records fail — the
    top-level success tells you nothing. You must read failedRecords. We
    chunk at the documented 100-record max and aggregate outcomes so the
    caller can re-submit only the failed subset.
    """
    all_ok: list[dict] = []
    all_failed: list[dict] = []
    for chunk in _chunked(records, MAX_RECORDS_PER_BATCH):
        resp = call_with_backoff(
            data_client.batch_create_memory_records,
            memoryId=memory_id,
            records=chunk,
        )
        ok = resp.get("successfulRecords", [])
        failed = resp.get("failedRecords", [])
        all_ok.extend(ok)
        all_failed.extend(failed)
        for r in failed:
            logger.error(
                "record %s failed: code=%s msg=%s",
                r.get("requestIdentifier"),
                r.get("errorCode"),
                r.get("errorMessage"),
            )
    if all_failed:
        _emit_metric("MemoryBatchRecordFailures", len(all_failed))
    return all_ok, all_failed


# --- Pattern 5: try/finally cleanup that can't leak -----------------------
class MemoryResource:
    """Context manager for an EPHEMERAL memory resource (tests, short jobs).

    Guarantees the resource is deleted even if the body raises — the classic
    leak is a memory orphaned (and billed) by an exception between create and
    delete. The cleanup is itself wrapped so a delete failure can't mask the
    original error.

    DO NOT use this for long-lived production memories — those are provisioned
    once out-of-band, not per request. See 03-production-checklist.md.
    """

    def __init__(self, control_client: Any, name: str, event_expiry_days: int = 30):
        if not (MIN_EVENT_EXPIRY_DAYS <= event_expiry_days <= MAX_EVENT_EXPIRY_DAYS):
            raise ValueError(
                f"event_expiry_days must be {MIN_EVENT_EXPIRY_DAYS}-{MAX_EVENT_EXPIRY_DAYS}; got {event_expiry_days}"
            )
        self._control = control_client
        self._name = name
        self._expiry = event_expiry_days
        self.memory_id: Optional[str] = None

    def __enter__(self) -> "MemoryResource":
        resp = call_with_backoff(
            self._control.create_memory,
            name=self._name,
            eventExpiryDuration=self._expiry,
            clientToken=str(uuid.uuid4()),
        )
        self.memory_id = resp["memory"]["id"]
        logger.info("created ephemeral memory %s", self.memory_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Returns False: never suppress the body's exception.
        if not self.memory_id:
            return False
        try:
            self._control.delete_memory(memoryId=self.memory_id, clientToken=str(uuid.uuid4()))
            logger.info("deleted ephemeral memory %s", self.memory_id)
        except ClientError as e:
            # Log; do NOT raise — a cleanup failure must not replace the real error.
            logger.error(
                "cleanup of %s failed (%s); may need manual deletion",
                self.memory_id,
                e.response["Error"]["Code"],
            )
        return False


# --- Pattern 6: cheap health check ----------------------------------------
def health_check(control_client: Any, memory_id: str) -> bool:
    """Readiness probe: is the memory resource present and ACTIVE?

    Cheap, control-plane-only, and safe to call from a health endpoint. A
    ResourceNotFoundException or a non-ACTIVE status means do not route
    memory-dependent traffic here yet. Wrapped so a transient blip reads as
    'not ready' rather than crashing the probe.
    """
    try:
        resp = call_with_backoff(control_client.get_memory, memoryId=memory_id)
        status = resp["memory"]["status"]
        if status != "ACTIVE":
            logger.warning("memory %s status=%s (not ACTIVE)", memory_id, status)
            return False
        return True
    except ClientError as e:
        logger.warning("health check failed (%s)", e.response["Error"]["Code"])
        return False


# --- Placeholder so the patterns are self-contained -----------------------
def _emit_metric(name: str, value: float) -> None:
    """Stand-in for your metrics pipeline (EMF, CloudWatch PutMetricData…).

    Replace with your telemetry. Kept trivial here so the patterns above read
    cleanly; the point is that every degradation/failure path is observable.
    """
    logger.debug("metric %s=%s", name, value)
