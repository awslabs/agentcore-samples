"""Tests for the report record dumps.

The extract stage dumps every Preview record it read, and the load stage dumps a matching
side-by-side artifact (old recordId, new recordId, described Preview record, transformed payload,
described target record). Both are chunked JSON arrays so the two can be diffed for verification at
any registry size.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.storage import JsonArrayWriter, S3Store


class FakeS3:
    def __init__(self, page_size: int | None = None):
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        # When set, list_objects_v2 truncates to this many keys per response, so pagination is
        # actually exercised rather than assumed.
        self.page_size = page_size
        self.list_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {"VersionId": "v1"}

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        prefix = kwargs.get("Prefix", "")
        # Deliberately returned unsorted, so a test asserting sorted output is testing S3Store and
        # not this fake.
        matching = sorted((key for key in self.objects if key.startswith(prefix)), reverse=True)
        start = int(kwargs.get("ContinuationToken", 0) or 0)
        window = self.page_size or len(matching) or 1
        page = matching[start : start + window]
        response: dict = {"Contents": [{"Key": key} for key in page]}
        if start + window < len(matching):
            response["NextContinuationToken"] = str(start + window)
        return response


class ListingKeysFromS3(unittest.TestCase):
    """``S3Store.list_keys``, which nothing covered.

    ``report`` and ``latest-run`` find a run by listing, so a truncated first page means the wrong
    run is reported as newest. No test double implemented ``list_objects_v2`` at all, which is why
    this went unexercised while a test named for "both stores" checked only the local one.
    """

    def _store(self, keys, page_size=None):
        client = FakeS3(page_size=page_size)
        for key in keys:
            client.objects[key] = b"{}"
        return client, S3Store(client, "bucket")

    def test_keys_are_prefix_filtered_and_sorted(self):
        _client, store = self._store(["reports/run_id=b/x.json", "reports/run_id=a/x.json", "runs/run_id=a/raw.jsonl"])
        self.assertEqual(
            store.list_keys("reports/run_id="),
            ["reports/run_id=a/x.json", "reports/run_id=b/x.json"],
        )

    def test_every_page_is_followed(self):
        keys = [f"reports/run_id={index:03d}/summary.json" for index in range(7)]
        client, store = self._store(keys, page_size=2)
        self.assertEqual(store.list_keys("reports/"), sorted(keys))
        # 7 keys at 2 per page: four requests, and the continuation token carried each time.
        self.assertEqual(len(client.list_calls), 4)
        self.assertNotIn("ContinuationToken", client.list_calls[0])
        self.assertIn("ContinuationToken", client.list_calls[1])

    def test_a_prefix_with_no_matches_is_empty(self):
        _client, store = self._store(["reports/run_id=a/x.json"])
        self.assertEqual(store.list_keys("nothing/"), [])

    def test_the_two_stores_answer_the_same_question(self):
        """The local store is the S3 store's twin, so listing has to agree between them."""
        import tempfile
        from pathlib import Path

        from migration_common.local_store import LocalStore

        keys = ["reports/run_id=b/x.json", "reports/run_id=a/x.json", "runs/run_id=a/raw.jsonl"]
        _client, s3_store = self._store(keys, page_size=1)
        with tempfile.TemporaryDirectory() as directory:
            local = LocalStore(Path(directory))
            for key in keys:
                local.put_json(key, {})
            self.assertEqual(
                s3_store.list_keys("reports/run_id="),
                local.list_keys("reports/run_id="),
            )


class JsonArrayWriterBehaviour(unittest.TestCase):
    def _writer(self, chunk_size=2):
        client = FakeS3()
        store = S3Store(client, "bucket")
        return client, JsonArrayWriter(store, "reports/run/records/mapping=m", basename="dump", chunk_size=chunk_size)

    def test_writes_valid_json_arrays_in_chunks(self):
        client, writer = self._writer(chunk_size=2)
        for index in range(5):
            writer.append({"i": index})
        keys = writer.close()

        self.assertEqual(
            keys,
            [
                "reports/run/records/mapping=m/dump-00000.json",
                "reports/run/records/mapping=m/dump-00001.json",
                "reports/run/records/mapping=m/dump-00002.json",
            ],
        )
        self.assertEqual(writer.record_count, 5)
        # Every part is independently parseable, and the concatenation preserves order.
        combined = []
        for key in keys:
            part = json.loads(client.objects[key].decode("utf-8"))
            self.assertIsInstance(part, list)
            combined.extend(part)
        self.assertEqual(combined, [{"i": i} for i in range(5)])

    def test_last_partial_chunk_is_flushed_once(self):
        client, writer = self._writer(chunk_size=10)
        writer.append({"only": True})
        writer.close()
        writer.close()  # idempotent: nothing buffered the second time
        self.assertEqual(len(client.puts), 1)

    def test_no_records_writes_nothing(self):
        client, writer = self._writer()
        self.assertEqual(writer.close(), [])
        self.assertEqual(client.puts, [])

    def test_objects_are_encrypted_json(self):
        client, writer = self._writer()
        writer.append({"a": 1})
        writer.close()
        put = client.puts[0]
        self.assertEqual(put["ContentType"], "application/json")
        self.assertEqual(put["ServerSideEncryption"], "AES256")

    def test_dump_rows_round_trip_unicode_and_nesting(self):
        client, writer = self._writer(chunk_size=10)
        row = {
            "oldRecordId": "old-1",
            "newRecordId": "new-1",
            "previewRecord": {
                "name": "多言語 ✅",
                "descriptors": {"mcp": {"server": {"inlineContent": '{"a":1}'}, "tools": {"inlineContent": "[]"}}},
            },
            "targetRecord": {
                "descriptors": {"mcpServer": {"data": '{"a":1}', "additionalData": {"tools": {"data": "[]"}}}}
            },
        }
        writer.append(row)
        writer.close()
        stored = json.loads(client.objects[writer.keys[0]].decode("utf-8"))
        self.assertEqual(stored[0], row)


class ComparisonRowShape(unittest.TestCase):
    """The load stage's comparison row must carry both described records and both ids."""

    def test_load_result_carries_the_described_target_record(self):
        """``upsert`` returns the target record from the poll it already performed.

        That is what lets the comparison row show the real target record without a second
        GetRegistryRecord call, and what makes ``record=None`` meaningful for a dry run.
        """
        from migration_common.registry_api import LoadResult

        loaded = LoadResult(
            action="created",
            new_record_id="new-1",
            record={"recordId": "new-1", "status": "DRAFT"},
        )
        self.assertEqual(loaded.record, {"recordId": "new-1", "status": "DRAFT"})
        self.assertIsNone(LoadResult(action="dryRun", new_record_id=None).record)


class CrosswalkCsvContract(unittest.TestCase):
    """The crosswalk CSV, checked against the production writer rather than a row built here.

    This replaces a test that constructed its own dictionary and then asserted the dictionary
    contained the keys it had just been given -- it passed with the load stage deleted. These
    exercise ``_crosswalk_csv`` and ``_CROSSWALK_COLUMNS`` themselves.
    """

    def test_both_ids_and_both_names_are_columns(self):
        from migration_common.jobs.transform_load import _CROSSWALK_COLUMNS

        # Repointing a dependency needs the old id; finding the record needs the new one. Both names
        # are needed because a duplicate-name collision means the target name is not the Preview name.
        for column in ("oldRecordId", "newRecordId", "previewName", "name", "targetStatus"):
            self.assertIn(column, _CROSSWALK_COLUMNS)

    def test_a_row_round_trips_through_the_writer(self):
        from migration_common.jobs.transform_load import _CROSSWALK_COLUMNS, _crosswalk_csv

        text = _crosswalk_csv([{"oldRecordId": "old-1", "newRecordId": "new-1", "name": "migrated-abc"}])
        header, row = text.splitlines()
        self.assertEqual(header, ",".join(_CROSSWALK_COLUMNS))
        values = dict(zip(_CROSSWALK_COLUMNS, row.split(",")))
        self.assertEqual(values["oldRecordId"], "old-1")
        self.assertEqual(values["newRecordId"], "new-1")
        # A column with no value is empty, not "None".
        self.assertEqual(values["displayName"], "")

    def test_a_name_that_looks_like_a_formula_is_neutralised(self):
        """Record names come from the source registry, and this file is opened in a spreadsheet.

        A leading =, +, - or @ makes a spreadsheet evaluate the cell instead of showing it, so the
        value is prefixed with an apostrophe -- which spreadsheets read as "literal text" and hide.
        """
        from migration_common.jobs.transform_load import _crosswalk_csv

        text = _crosswalk_csv([{"oldRecordId": "old-1", "name": '=HYPERLINK("http://evil","click")'}])
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)

    def test_ordinary_values_are_not_prefixed(self):
        from migration_common.jobs.transform_load import _crosswalk_csv

        text = _crosswalk_csv([{"oldRecordId": "old-1", "name": "migrated-abc"}])
        self.assertIn("migrated-abc", text)
        self.assertNotIn("'migrated-abc", text)


if __name__ == "__main__":
    unittest.main()
