"""Unit tests for the LTM stream consumer.

Run from the sample directory: ``python -m pytest tests``. No AWS access is needed;
a fake target client stands in for ``bedrock-agentcore``.
"""

import base64
import json

import pytest
from agentcore_replication.record_map import DynamoDBRecordMap, InMemoryRecordMap, MappedRecord
from agentcore_replication.stream_consumer import _to_epoch, process_kinesis_records, replicate_stream_event
from botocore.exceptions import ClientError

TARGET = "mem-target"


class FakeTargetClient:
    """Records calls and mimics AgentCore: a repeated clientToken returns the first response."""

    def __init__(self, failed_records=None):
        self.calls = []
        self.records = {}
        self._responses_by_token = {}
        self._failed_records = failed_records or []

    def batch_create_memory_records(self, memoryId, records, clientToken):
        self.calls.append(("create", clientToken, records))
        if self._failed_records:
            return {"successfulRecords": [], "failedRecords": self._failed_records}
        if clientToken not in self._responses_by_token:
            record_id = f"tgt-{len(self.records) + 1}"
            self.records[record_id] = records[0]["content"]["text"]
            self._responses_by_token[clientToken] = {
                "successfulRecords": [
                    {"memoryRecordId": record_id, "requestIdentifier": records[0]["requestIdentifier"]}
                ],
                "failedRecords": [],
            }
        return self._responses_by_token[clientToken]

    def batch_update_memory_records(self, memoryId, records):
        self.calls.append(("update", None, records))
        self.records[records[0]["memoryRecordId"]] = records[0]["content"]["text"]
        return {"successfulRecords": [{"memoryRecordId": records[0]["memoryRecordId"]}], "failedRecords": []}

    def batch_delete_memory_records(self, memoryId, records):
        self.calls.append(("delete", None, records))
        record_id = records[0]["memoryRecordId"]
        if self.records.pop(record_id, None) is None:
            return {
                "successfulRecords": [],
                "failedRecords": [{"memoryRecordId": record_id, "status": "FAILED", "errorCode": 404}],
            }
        return {"successfulRecords": [{"memoryRecordId": record_id}], "failedRecords": []}


def event(event_type, record_id="src-1", text="likes tea", event_time="2026-10-09T12:00:00Z"):
    return {
        "eventType": event_type,
        "memoryId": "mem-source",
        "memoryRecordId": record_id,
        "memoryRecordText": text,
        "namespaces": ["/facts/user-1"],
        "eventTime": event_time,
    }


def replicate(stream_event, client, record_map):
    return replicate_stream_event(stream_event, client, TARGET, record_map)


def test_a_redelivered_create_reuses_the_same_client_token():
    first, second = FakeTargetClient(), FakeTargetClient()
    replicate(event("MemoryRecordCreated"), first, InMemoryRecordMap())
    replicate(event("MemoryRecordCreated"), second, InMemoryRecordMap())
    assert first.calls[0][1] == second.calls[0][1]


def test_a_redelivered_create_is_skipped_once_the_record_is_mapped():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    assert replicate(event("MemoryRecordCreated"), client, record_map) == "replicated"
    assert replicate(event("MemoryRecordCreated"), client, record_map) == "skipped"
    assert len(client.records) == 1


def test_an_update_is_applied_to_the_mapped_standby_record():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    replicate(event("MemoryRecordCreated"), client, record_map)
    replicate(event("MemoryRecordUpdated", text="likes coffee", event_time="2026-10-09T12:05:00Z"), client, record_map)
    assert client.calls[-1][0] == "update"
    assert client.records == {"tgt-1": "likes coffee"}


def test_an_update_that_is_not_newer_than_the_last_applied_change_is_skipped():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    replicate(event("MemoryRecordCreated", event_time="2026-10-09T12:05:00Z"), client, record_map)
    outcome = replicate(event("MemoryRecordUpdated", text="stale"), client, record_map)
    assert outcome == "skipped"
    assert client.records == {"tgt-1": "likes tea"}


def test_an_update_for_an_unmapped_record_creates_it():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    replicate(event("MemoryRecordUpdated", text="likes coffee"), client, record_map)
    assert client.calls[0][0] == "create"
    assert record_map.get("src-1").target_record_id == "tgt-1"


def test_a_delete_removes_the_mapped_standby_record():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    replicate(event("MemoryRecordCreated"), client, record_map)
    replicate(event("MemoryRecordDeleted", event_time="2026-10-09T12:10:00Z"), client, record_map)
    assert client.records == {}
    assert record_map.get("src-1").deleted


def test_a_create_that_arrives_after_its_delete_is_skipped():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    replicate(event("MemoryRecordDeleted", event_time="2026-10-09T12:10:00Z"), client, record_map)
    assert replicate(event("MemoryRecordCreated"), client, record_map) == "skipped"
    assert client.records == {}


def test_a_delete_of_a_record_already_gone_from_the_standby_counts_as_done():
    client, record_map = FakeTargetClient(), InMemoryRecordMap()
    record_map.put("src-1", MappedRecord(target_record_id="tgt-missing", event_time=0.0))
    assert replicate(event("MemoryRecordDeleted"), client, record_map) == "replicated"
    assert record_map.get("src-1").deleted


def test_a_stream_event_time_with_nanoseconds_is_parsed_rather_than_replaced_by_now():
    # The stream sends 9 fractional digits; datetime.fromisoformat rejects them before Python 3.11.
    assert _to_epoch("2026-10-09T15:55:36.241618788Z") == pytest.approx(1791561336.241618, abs=1e-3)


def test_streaming_enabled_and_unknown_events_are_skipped_without_failing():
    client = FakeTargetClient()
    raw = [
        {"kinesis": {"data": base64.b64encode(json.dumps({"memoryStreamEvent": e}).encode()).decode()}}
        for e in ({"eventType": "StreamingEnabled"}, {"eventType": "SomethingNew"})
    ]
    stats = process_kinesis_records(raw, client, TARGET, InMemoryRecordMap())
    assert (stats.skipped, stats.failed, client.calls) == (2, 0, [])


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_throttled_or_server_side_failed_record_raises_so_the_batch_is_retried(code):
    client = FakeTargetClient(failed_records=[{"status": "FAILED", "errorCode": code, "errorMessage": "try again"}])
    with pytest.raises(ClientError):
        replicate(event("MemoryRecordCreated"), client, InMemoryRecordMap())


def test_a_client_side_failed_record_is_terminal_and_leaves_the_record_unmapped():
    client, record_map = FakeTargetClient(failed_records=[{"status": "FAILED", "errorCode": 400}]), InMemoryRecordMap()
    with pytest.raises(RuntimeError):
        replicate(event("MemoryRecordCreated"), client, record_map)
    assert record_map.get("src-1") is None


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key, ConsistentRead):
        item = self.items.get(Key["sourceRecordId"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item["sourceRecordId"]] = Item


def test_the_dynamodb_record_map_round_trips_a_tombstone():
    record_map = DynamoDBRecordMap(FakeTable())
    record_map.put("src-1", MappedRecord(target_record_id=None, event_time=1.5, deleted=True))
    assert record_map.get("src-1") == MappedRecord(target_record_id=None, event_time=1.5, deleted=True)
    assert record_map.get("src-2") is None
