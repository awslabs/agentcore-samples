"""Customer-driven cross-region replication for Amazon Bedrock AgentCore Memory.

Replicates **both** memory layers from a source region to a target region:

* **LTM (long-term records) via record streaming** — the source memory is
  configured with ``streamDeliveryResources`` (``MEMORY_RECORDS`` /
  ``FULL_CONTENT``), so extracted records are published to a Kinesis Data Stream.
  A consumer (:mod:`stream_consumer`, run as a Lambda or locally) re-creates each
  record in the target via ``BatchCreateMemoryRecords``, using the source
  ``memoryRecordId`` as the ``requestIdentifier`` so replays are idempotent.
* **STM (short-term events) via dual-write ``CreateEvent``** —
  :class:`DualRegionEventWriter` writes every conversation turn to both regions:
  the source normally (triggering extraction, which feeds the stream above) and
  the target with ``extractionMode="SKIP"`` (history only, no re-extraction).

``extractionMode="SKIP"`` on the target STM write is what keeps the two paths
from colliding: LTM arrives via the stream, so the target must NOT re-extract the
replicated events into duplicate records.

See ``README.md`` for deployment instructions.
"""

from .stream_consumer import (
    StreamStats,
    make_target_client,
    process_kinesis_records,
    replicate_stream_event,
    stream_delivery_resources,
)
from .dual_writer import DualRegionEventWriter

__all__ = [
    # LTM via record streaming
    "StreamStats",
    "make_target_client",
    "process_kinesis_records",
    "replicate_stream_event",
    "stream_delivery_resources",
    # STM via dual-write CreateEvent (extractionMode="SKIP" on target)
    "DualRegionEventWriter",
]
