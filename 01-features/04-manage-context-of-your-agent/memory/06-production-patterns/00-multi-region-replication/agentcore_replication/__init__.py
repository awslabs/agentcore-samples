"""Customer-driven cross-region replication for Amazon Bedrock AgentCore Memory.

Replicates **both** memory layers from a source region to a target region:

* **LTM (long-term records) via record streaming** — the source memory is
  configured with ``streamDeliveryResources`` (``MEMORY_RECORDS`` /
  ``FULL_CONTENT``), so extracted records are published to a Kinesis Data Stream.
  A consumer (:mod:`stream_consumer`, run as a Lambda or locally) applies each
  create, update, and delete to the target. A :mod:`record_map` tracks which
  target record each source record became, and creates send a ``clientToken``
  derived from the stream event, so replays don't duplicate records.
* **STM (short-term events) via dual-write ``CreateEvent``** —
  :class:`DualRegionEventWriter` writes every conversation turn to both regions:
  the source normally (triggering extraction, which feeds the stream above) and
  the target with ``extractionMode="SKIP"`` (history only, no re-extraction).

``extractionMode="SKIP"`` on the target STM write is what keeps the two paths
from colliding: LTM arrives via the stream, so the target must NOT re-extract the
replicated events into duplicate records.

See ``README.md`` for deployment instructions.
"""

from .dual_writer import DualRegionEventWriter
from .record_map import DynamoDBRecordMap, InMemoryRecordMap, MappedRecord, RecordMap
from .stream_consumer import (
    StreamStats,
    make_target_client,
    process_kinesis_records,
    replicate_stream_event,
    stream_delivery_resources,
)

__all__ = [
    # STM via dual-write CreateEvent (extractionMode="SKIP" on target)
    "DualRegionEventWriter",
    # LTM via record streaming
    "DynamoDBRecordMap",
    "InMemoryRecordMap",
    "MappedRecord",
    "RecordMap",
    "StreamStats",
    "make_target_client",
    "process_kinesis_records",
    "replicate_stream_event",
    "stream_delivery_resources",
]
