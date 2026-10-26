"""STM replication via dual-write ``CreateEvent`` (the ``extractionMode`` story).

Short-term memory (the raw conversation events) is replicated at write time, not
after the fact: when the agent records a turn, it writes the **same event to both
regions** —

* **source region** — a normal ``CreateEvent`` (no ``extractionMode``). The event
  lands in STM *and* feeds the long-term extraction pipeline, which distills
  durable facts into LTM. Those LTM records are what the Kinesis record stream
  replicates to the target (see :mod:`stream_consumer`).
* **target region** — ``CreateEvent(extractionMode="SKIP")``. The event lands in
  the target's STM for conversation history / failover replay, but is **excluded
  from long-term extraction** — because the corresponding LTM records are already
  arriving over the stream. Without ``SKIP`` the target would re-extract the same
  facts, producing duplicate LTM records and double extraction cost.

This split is the whole point of the sample: **LTM replicates via streaming, STM
replicates via dual-write CreateEvent, and ``extractionMode="SKIP"`` on the
target is what keeps the two paths from colliding.**

Cross-region event identity
----------------------------
AgentCore derives an event's sort key from ``(eventTimestamp, clientToken)``. To
get a stable, idempotent event identity in both regions, pass an explicit
``clientToken`` and the same ``eventTimestamp`` to both writes — don't rely on
the SDK auto-generating either. :class:`DualRegionEventWriter` does this for you.
"""

import logging
import uuid
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_EXTRACTION_MODE_SKIP = "SKIP"


class DualRegionEventWriter:
    """Write conversation events to a source memory and a target replica.

    Parameters
    ----------
    source_memory_id, target_memory_id:
        Memory resource IDs in each region.
    source_region, target_region:
        AWS regions, e.g. ``us-east-1`` / ``us-west-2``.
    session:
        Optional pre-configured ``boto3.Session``.
    """

    def __init__(
        self,
        source_memory_id: str,
        target_memory_id: str,
        source_region: str,
        target_region: str,
        session: Optional[boto3.Session] = None,
    ):
        self.source_memory_id = source_memory_id
        self.target_memory_id = target_memory_id
        session = session or boto3.Session()
        self.source = session.client("bedrock-agentcore", region_name=source_region)
        self.target = session.client("bedrock-agentcore", region_name=target_region)

    def record_turn(
        self,
        actor_id: str,
        session_id: str,
        role: str,
        text: str,
        event_timestamp: Optional[float] = None,
        client_token: Optional[str] = None,
    ) -> dict:
        """Record one conversation turn in BOTH regions.

        The source write triggers normal LTM extraction; the target write uses
        ``extractionMode="SKIP"`` so it stores history only. Returns a dict with
        both event responses and the shared ``clientToken`` / ``eventTimestamp``
        used (so callers can correlate the two regions).
        """
        if event_timestamp is None:
            # A single timestamp shared by both writes keeps the event identity
            # aligned across regions. (Date.now-style call is fine here — this is
            # the live app path, not a replayable workflow.)
            import time

            event_timestamp = time.time()
        # Shared, explicit client token => deterministic, idempotent identity in
        # both regions (AgentCore keys events on (eventTimestamp, clientToken)).
        client_token = client_token or f"turn-{uuid.uuid4().hex}"

        payload = [{"conversational": {"content": {"text": text}, "role": role}}]

        # 1) Source: normal write -> STM + triggers extraction (-> stream -> LTM).
        source_resp = self.source.create_event(
            memoryId=self.source_memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=event_timestamp,
            payload=payload,
            clientToken=client_token,
        )

        # 2) Target: SKIP extraction -> STM history only (LTM comes via stream).
        target_resp = self._create_event_idempotent(
            self.target,
            self.target_memory_id,
            actor_id,
            session_id,
            event_timestamp,
            payload,
            client_token,
            extraction_mode=_EXTRACTION_MODE_SKIP,
        )

        return {
            "clientToken": client_token,
            "eventTimestamp": event_timestamp,
            "source_event": source_resp.get("event", {}),
            "target_event": (target_resp or {}).get("event", {}),
        }

    @staticmethod
    def _create_event_idempotent(
        client,
        memory_id,
        actor_id,
        session_id,
        event_timestamp,
        payload,
        client_token,
        extraction_mode=None,
    ):
        """CreateEvent that treats an idempotent "already exists" collision as OK."""
        kwargs = dict(
            memoryId=memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=event_timestamp,
            payload=payload,
            clientToken=client_token,
        )
        if extraction_mode:
            kwargs["extractionMode"] = extraction_mode
        try:
            return client.create_event(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ConflictException", "ValidationException") and ("exist" in str(exc).lower()):
                logger.info("event already exists (idempotent), token=%s", client_token)
                return None
            raise
