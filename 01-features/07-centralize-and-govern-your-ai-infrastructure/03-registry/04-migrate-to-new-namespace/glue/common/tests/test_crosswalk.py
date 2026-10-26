"""Tests for the old->new recordId crosswalk output and the S3 text writer.

Covers the pure CSV renderer (header, RFC 4180 quoting/round-trip, header-only when empty),
``S3Store.put_text`` (encryption + content type), and ``_write_crosswalks`` (one CSV per
registry at a predictable key).
"""

from __future__ import annotations

import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.jobs.transform_load import (
    _CROSSWALK_COLUMNS,
    _crosswalk_csv,
    _write_crosswalks,
)
from migration_common.storage import S3Store


class _FakeS3Client:
    def __init__(self):
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"VersionId": "v1"}


class CrosswalkCsv(unittest.TestCase):
    def test_header_and_roundtrip_with_special_characters(self):
        rows = [
            {
                "oldRecordId": "rec-1",
                "newRecordId": "rec-new-1",
                "name": "migrated-abc",
                "displayName": 'Weird, name "quoted"',
                "recordType": "MCP",
                "recordVersion": "1",
                "action": "created",
                "status": "SUCCEEDED",
            },
            {
                "oldRecordId": "rec-2",
                "newRecordId": "",
                "name": "migrated-def",
                "displayName": "Second",
                "recordType": "CUSTOM",
                "recordVersion": None,
                "action": "failed",
                "status": "FAILED",
            },
        ]
        parsed = list(csv.reader(io.StringIO(_crosswalk_csv(rows))))
        self.assertEqual(parsed[0], list(_CROSSWALK_COLUMNS))
        self.assertEqual(parsed[1][0], "rec-1")
        # Read by column, not position: the header is the contract, and columns get added.
        display_index = _CROSSWALK_COLUMNS.index("displayName")
        self.assertEqual(parsed[1][display_index], 'Weird, name "quoted"')  # comma+quote survive
        # None recordVersion is rendered as an empty field, not the string "None".
        version_index = _CROSSWALK_COLUMNS.index("recordVersion")
        self.assertEqual(parsed[2][version_index], "")

    def test_empty_rows_emit_header_only(self):
        text = _crosswalk_csv([])
        self.assertEqual(text.strip(), ",".join(_CROSSWALK_COLUMNS))


class PutText(unittest.TestCase):
    def test_encrypts_and_sets_content_type(self):
        client = _FakeS3Client()
        store = S3Store(client, "bucket")
        store.put_text("some/key.csv", "a,b\n1,2\n", content_type="text/csv")
        self.assertEqual(len(client.puts), 1)
        put = client.puts[0]
        self.assertEqual(put["Bucket"], "bucket")
        self.assertEqual(put["Key"], "some/key.csv")
        self.assertEqual(put["ContentType"], "text/csv")
        self.assertEqual(put["ServerSideEncryption"], "AES256")
        self.assertEqual(put["Body"], b"a,b\n1,2\n")


class WriteCrosswalks(unittest.TestCase):
    def test_one_file_per_registry_even_when_empty(self):
        client = _FakeS3Client()
        store = S3Store(client, "bucket")
        summaries = {"map-a": {}, "map-b": {}}
        rows = {
            "map-a": [
                {
                    "oldRecordId": "o1",
                    "newRecordId": "n1",
                    "name": "x",
                    "recordType": "MCP",
                    "action": "created",
                    "status": "SUCCEEDED",
                }
            ]
        }
        locations = _write_crosswalks(store, "reports/run/attempt/crosswalk", summaries, rows)

        self.assertEqual(
            locations,
            {
                "map-a": "s3://bucket/reports/run/attempt/crosswalk/mapping=map-a.csv",
                "map-b": "s3://bucket/reports/run/attempt/crosswalk/mapping=map-b.csv",
            },
        )
        keys = sorted(p["Key"] for p in client.puts)
        self.assertEqual(
            keys,
            [
                "reports/run/attempt/crosswalk/mapping=map-a.csv",
                "reports/run/attempt/crosswalk/mapping=map-b.csv",
            ],
        )
        # map-b had no records: header-only body.
        map_b = next(p for p in client.puts if p["Key"].endswith("map-b.csv"))
        self.assertEqual(map_b["Body"].decode("utf-8").strip(), ",".join(_CROSSWALK_COLUMNS))


if __name__ == "__main__":
    unittest.main()
