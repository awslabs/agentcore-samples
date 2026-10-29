"""Source-to-target record ID map for LTM replication.

The target memory assigns its own ``memoryRecordId`` to every record that the
consumer creates, and a ``MemoryRecordDeleted`` stream event carries only the
source IDs. To apply updates and deletes, the consumer has to remember which
target record each source record became, and the time of the last change that
it applied (so a redelivered or out-of-order event is skipped, not re-applied).

Two implementations share one small interface:

* :class:`DynamoDBRecordMap` — the Lambda path, backed by the table in
  ``infra/streaming-stack.yaml``.
* :class:`InMemoryRecordMap` — the local demo and the unit tests.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class MappedRecord:
    """What the consumer knows about one replicated source record."""

    target_record_id: str | None  # None for a tombstone of a never-replicated record
    event_time: float  # epoch seconds of the last change applied
    deleted: bool = False


class RecordMap(Protocol):
    def get(self, source_record_id: str) -> MappedRecord | None: ...

    def put(self, source_record_id: str, record: MappedRecord) -> None: ...


class InMemoryRecordMap:
    """Process-local map: fine for a demo loop, not for Lambda (each container has its own)."""

    def __init__(self):
        self._records = {}

    def get(self, source_record_id: str) -> MappedRecord | None:
        return self._records.get(source_record_id)

    def put(self, source_record_id: str, record: MappedRecord) -> None:
        self._records[source_record_id] = record


class DynamoDBRecordMap:
    """Durable map in a DynamoDB table keyed by ``sourceRecordId`` (a boto3 ``Table`` resource)."""

    def __init__(self, table):
        self._table = table

    def get(self, source_record_id: str) -> MappedRecord | None:
        item = self._table.get_item(Key={"sourceRecordId": source_record_id}, ConsistentRead=True).get("Item")
        if not item:
            return None
        return MappedRecord(
            target_record_id=item.get("targetRecordId"),
            event_time=float(item["eventTime"]),
            deleted=bool(item.get("deleted", False)),
        )

    def put(self, source_record_id: str, record: MappedRecord) -> None:
        item = {
            "sourceRecordId": source_record_id,
            "eventTime": Decimal(str(record.event_time)),
            "deleted": record.deleted,
        }
        if record.target_record_id:
            item["targetRecordId"] = record.target_record_id
        self._table.put_item(Item=item)
