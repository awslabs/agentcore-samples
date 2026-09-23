"""The local store has to make the same promises S3 does, or a local run is not trustworthy.

Two of those promises carry the whole design:

* a run id cannot be silently reused (S3 gets this from ``If-None-Match: *``)
* staged bytes are pinned, so a file edited between extract and load is refused (S3 gets this from
  object version ids)

The rest is round-tripping and ordering. Everything here is real; there is no fake in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.local_store import LocalStore
from migration_common.storage import JsonArrayWriter
from migration_common.util import json_dumps


class LocalStoreBasics(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(self.temp.name)

    def test_json_round_trips_and_creates_parent_directories(self):
        self.store.put_json("reports/run_id=r1/summary.json", {"status": "SUCCEEDED"})
        self.assertEqual(self.store.get_json("reports/run_id=r1/summary.json"), {"status": "SUCCEEDED"})
        self.assertTrue((Path(self.temp.name) / "reports/run_id=r1/summary.json").is_file())

    def test_text_round_trips(self):
        self.store.put_text("reports/x.csv", "a,b\n1,2\n", content_type="text/csv")
        path = Path(self.temp.name) / "reports/x.csv"
        self.assertEqual(path.read_text(encoding="utf-8"), "a,b\n1,2\n")

    def test_a_missing_file_reads_as_none_rather_than_raising(self):
        # Optional artifacts (a watermark on a first run) rely on this.
        self.assertIsNone(self.store.get_json_if_present("state/watermarks/mapping=a.json"))

    def test_location_is_a_usable_path(self):
        location = self.store.location("reports/run_id=r1/summary.json")
        self.assertTrue(location.startswith(str(Path(self.temp.name).resolve())))
        self.assertTrue(location.endswith("reports/run_id=r1/summary.json"))
        self.assertEqual(self.store.location(), str(Path(self.temp.name).resolve()))

    def test_a_key_cannot_escape_the_staging_directory(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            self.store.put_json("../escaped.json", {"nope": True})


class RunIdsCannotBeSilentlyReused(unittest.TestCase):
    """``put_json_if_absent`` is the run/attempt lock: the second writer must lose."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(self.temp.name)

    def test_the_second_write_is_refused_and_the_first_survives(self):
        key = "state/locks/run_id=r1/extract.json"
        self.store.put_json_if_absent(key, {"runId": "r1", "attempt": "first"})
        with self.assertRaisesRegex(RuntimeError, "Immutable local file already exists"):
            self.store.put_json_if_absent(key, {"runId": "r1", "attempt": "second"})
        self.assertEqual(self.store.get_json(key)["attempt"], "first")

    def test_the_error_names_the_file_so_it_can_be_inspected(self):
        key = "state/locks/run_id=r1/extract.json"
        self.store.put_json_if_absent(key, {})
        with self.assertRaises(RuntimeError) as caught:
            self.store.put_json_if_absent(key, {})
        self.assertIn(str(Path(self.temp.name).resolve()), str(caught.exception))


class StagedBytesArePinned(unittest.TestCase):
    """The load stage reconciles staged data against the manifest before it processes anything.

    S3 pins bytes by version id. Here the content hash is the version id, so the same tampering the
    bucket-backed store would catch is caught on a filesystem too.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(self.temp.name)
        self.key = "runs/run_id=r1/raw/mapping=m/part-00000.jsonl"
        self.records = [{"recordId": "rec-1"}, {"recordId": "rec-2"}, {"recordId": "rec-3"}]
        self.manifest = self.store.put_json_lines(self.key, self.records)

    def test_the_manifest_entry_describes_the_object(self):
        body = ("\n".join(json_dumps(record) for record in self.records) + "\n").encode("utf-8")
        self.assertEqual(self.manifest["key"], self.key)
        self.assertEqual(self.manifest["recordCount"], 3)
        self.assertEqual(self.manifest["sizeBytes"], len(body))
        self.assertEqual(self.manifest["sha256"], hashlib.sha256(body).hexdigest())
        # No object versions on a filesystem, so the hash is the version.
        self.assertEqual(self.manifest["versionId"], self.manifest["sha256"])

    def test_inspecting_an_untouched_object_matches_the_manifest(self):
        actual = self.store.inspect_json_lines_object(self.manifest)
        for field in ("recordCount", "sha256", "sizeBytes", "versionId"):
            self.assertEqual(actual[field], self.manifest[field], field)

    def test_an_edited_staged_file_no_longer_matches(self):
        path = Path(self.temp.name) / self.key
        path.write_text(path.read_text(encoding="utf-8").replace("rec-2", "rec-9"), encoding="utf-8")
        actual = self.store.inspect_json_lines_object(self.manifest)
        self.assertNotEqual(actual["sha256"], self.manifest["sha256"])
        self.assertNotEqual(actual["versionId"], self.manifest["versionId"])

    def test_a_truncated_staged_file_no_longer_matches(self):
        path = Path(self.temp.name) / self.key
        lines = path.read_text(encoding="utf-8").splitlines()[:2]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        actual = self.store.inspect_json_lines_object(self.manifest)
        self.assertEqual(actual["recordCount"], 2)
        self.assertNotEqual(actual["sha256"], self.manifest["sha256"])

    def test_a_missing_staged_file_is_reported_clearly(self):
        (Path(self.temp.name) / self.key).unlink()
        with self.assertRaisesRegex(RuntimeError, "Staged object is missing"):
            self.store.inspect_json_lines_object(self.manifest)

    def test_records_are_yielded_in_manifest_order_across_objects(self):
        second = self.store.put_json_lines("runs/run_id=r1/raw/mapping=m/part-00001.jsonl", [{"recordId": "rec-4"}])
        pairs = list(self.store.iter_json_lines_objects([self.manifest, second]))
        self.assertEqual([record["recordId"] for _key, record in pairs], ["rec-1", "rec-2", "rec-3", "rec-4"])
        self.assertEqual([key for key, _record in pairs].count(self.key), 3)

    def test_read_ahead_is_accepted_and_changes_nothing(self):
        # Interface parity with the S3 store, whose read-ahead hides network latency.
        pairs = list(self.store.iter_json_lines_objects([self.manifest], read_ahead=4))
        self.assertEqual(len(pairs), 3)

    def test_an_empty_record_set_still_produces_a_valid_manifest_entry(self):
        empty = self.store.put_json_lines("runs/run_id=r1/raw/mapping=m/part-00002.jsonl", [])
        self.assertEqual(empty["recordCount"], 0)
        self.assertEqual(empty["sizeBytes"], 0)
        self.assertEqual(list(self.store.iter_json_lines_objects([empty])), [])


class ReportWritersWorkAgainstTheLocalStore(unittest.TestCase):
    def test_json_array_writer_chunks_into_parts(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = LocalStore(temp.name)
        writer = JsonArrayWriter(store, "reports/run_id=r1/dump", basename="part", chunk_size=2)
        for index in range(5):
            writer.append({"index": index})
        keys = writer.close()

        self.assertEqual(
            keys,
            [
                "reports/run_id=r1/dump/part-00000.json",
                "reports/run_id=r1/dump/part-00001.json",
                "reports/run_id=r1/dump/part-00002.json",
            ],
        )
        rows = [row for key in keys for row in store.get_json(key)]
        self.assertEqual([row["index"] for row in rows], [0, 1, 2, 3, 4])
        # Each part is a self-contained JSON array, as the reports promise.
        first = json.loads((Path(temp.name) / keys[0]).read_text(encoding="utf-8"))
        self.assertIsInstance(first, list)


if __name__ == "__main__":
    unittest.main()
