"""Tests for reading staged JSONL back out of S3, including bounded read-ahead.

Read-ahead exists so the load stage does not stall on an S3 GET between batches of records. It
must never change what the load sees: same records, same order, same pinned versions. It must also
stay memory-bounded, which means large objects are streamed rather than buffered.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import storage
from migration_common.storage import S3Store


class _Body:
    """A body that can be streamed line by line or read whole, tracking which was used."""

    def __init__(self, data: bytes, usage: list[str], key: str) -> None:
        self._data = data
        self._usage = usage
        self._key = key

    def iter_lines(self):
        self._usage.append(f"stream:{self._key}")
        yield from self._data.split(b"\n")

    def read(self):
        self._usage.append(f"buffer:{self._key}")
        return self._data


class FakeS3:
    def __init__(self, delay: float = 0.0) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.gets: list[tuple[str, str]] = []
        self.usage: list[str] = []
        self.delay = delay
        self.active = 0
        self.peak_active = 0
        self._lock = threading.Lock()

    def stage(self, key: str, records: list[dict], *, version_id: str = "v1") -> dict:
        body = "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
        self.objects[(key, version_id)] = body
        return {"key": key, "versionId": version_id, "sizeBytes": len(body), "recordCount": len(records)}

    def get_object(self, Bucket: str, Key: str, VersionId: str | None = None):
        with self._lock:
            self.gets.append((Key, str(VersionId)))
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        if self.delay:
            time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return {"Body": _Body(self.objects[(Key, str(VersionId))], self.usage, Key)}


def records(prefix: str, count: int) -> list[dict]:
    return [{"oldRecordId": f"{prefix}-{index}"} for index in range(count)]


class StagedObjectReading(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.store = S3Store(self.s3, "bucket")
        self.inventory = [
            self.s3.stage("runs/raw/part-00000.jsonl", records("a", 3)),
            self.s3.stage("runs/raw/part-00001.jsonl", records("b", 2)),
            self.s3.stage("runs/raw/part-00002.jsonl", records("c", 4)),
        ]
        self.expected = (
            [("runs/raw/part-00000.jsonl", f"a-{i}") for i in range(3)]
            + [("runs/raw/part-00001.jsonl", f"b-{i}") for i in range(2)]
            + [("runs/raw/part-00002.jsonl", f"c-{i}") for i in range(4)]
        )

    def _read(self, **kwargs):
        return [
            (key, record["oldRecordId"]) for key, record in self.store.iter_json_lines_objects(self.inventory, **kwargs)
        ]

    def test_serial_read_returns_every_record_in_order(self):
        self.assertEqual(self._read(), self.expected)

    def test_read_ahead_returns_exactly_the_same_records_in_the_same_order(self):
        for read_ahead in (1, 2, 5):
            with self.subTest(read_ahead=read_ahead):
                self.s3.usage.clear()
                self.assertEqual(self._read(read_ahead=read_ahead), self.expected)

    def test_every_object_is_read_once_at_its_pinned_version(self):
        self._read(read_ahead=2)
        # Prefetch means GETs may be issued in any order; each object must still be fetched
        # exactly once, and always at the version the manifest pinned.
        self.assertEqual(
            sorted(self.s3.gets),
            [
                ("runs/raw/part-00000.jsonl", "v1"),
                ("runs/raw/part-00001.jsonl", "v1"),
                ("runs/raw/part-00002.jsonl", "v1"),
            ],
        )

    def test_read_ahead_of_zero_streams_and_never_buffers(self):
        self._read()
        self.assertTrue(all(entry.startswith("stream:") for entry in self.s3.usage), self.s3.usage)

    def test_a_single_object_run_is_never_prefetched(self):
        self.inventory = self.inventory[:1]
        list(self.store.iter_json_lines_objects(self.inventory, read_ahead=4))
        self.assertEqual(self.s3.usage, ["stream:runs/raw/part-00000.jsonl"])

    def test_oversized_objects_are_streamed_not_buffered(self):
        # A wide recordsPerObject can make one object huge; buffering several would be an OOM.
        self.inventory[1] = dict(self.inventory[1], sizeBytes=storage._MAX_PREFETCH_BYTES + 1)
        self.assertEqual(self._read(read_ahead=2), self.expected)
        self.assertIn("stream:runs/raw/part-00001.jsonl", self.s3.usage)
        self.assertIn("buffer:runs/raw/part-00000.jsonl", self.s3.usage)

    def test_objects_without_a_declared_size_are_streamed(self):
        self.inventory = [
            {key: value for key, value in entry.items() if key != "sizeBytes"} for entry in self.inventory
        ]
        self.assertEqual(self._read(read_ahead=2), self.expected)
        self.assertTrue(all(entry.startswith("stream:") for entry in self.s3.usage), self.s3.usage)

    def test_reads_actually_overlap(self):
        slow = FakeS3(delay=0.05)
        store = S3Store(slow, "bucket")
        inventory = [
            slow.stage("runs/raw/part-00000.jsonl", records("a", 1)),
            slow.stage("runs/raw/part-00001.jsonl", records("b", 1)),
            slow.stage("runs/raw/part-00002.jsonl", records("c", 1)),
        ]
        # Consume lazily, one record at a time, exactly as the load stage does.
        consumed = [record["oldRecordId"] for _key, record in store.iter_json_lines_objects(inventory, read_ahead=2)]
        self.assertEqual(consumed, ["a-0", "b-0", "c-0"])
        self.assertGreater(slow.peak_active, 1, "objects were fetched one after another")

    def test_non_object_line_is_rejected(self):
        self.s3.objects[("runs/raw/bad.jsonl", "v1")] = b'{"ok": 1}\n"not-an-object"\n'
        inventory = [{"key": "runs/raw/bad.jsonl", "versionId": "v1", "sizeBytes": 10}]
        with self.assertRaisesRegex(ValueError, "Expected JSON object"):
            list(self.store.iter_json_lines_objects(inventory, read_ahead=1))

    def test_missing_manifest_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "versionId"):
            list(self.store.iter_json_lines_objects([{"key": "runs/raw/x.jsonl"}]))


if __name__ == "__main__":
    unittest.main()
