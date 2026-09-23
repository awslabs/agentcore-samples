"""Tests for incremental-load watermarks.

Covers cutoff selection (explicit changedAfter vs saved watermark vs no basis at all), the
overlap buffer, candidate construction, and the commit rules that keep a failed load from
advancing the watermark.

Also covers the id map stored beside them -- what it remembers across runs, and what a corrupt one
does. What the *loader* does with a recorded id is a matching question, so it lives with the other
matching tests in test_source_index.py.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import watermark
from migration_common.storage import S3Store


class _NoSuchKey(Exception):
    pass


class _Exceptions:
    NoSuchKey = _NoSuchKey


class FakeS3:
    exceptions = _Exceptions()

    def __init__(self, objects: dict[str, str] | None = None):
        self.objects = dict(objects or {})
        self.puts: list[dict] = []

    def get_object(self, Bucket: str, Key: str):
        if Key not in self.objects:
            raise _NoSuchKey(Key)

        class _Body:
            def __init__(self, data: bytes):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.objects[Key].encode("utf-8"))}

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"].decode("utf-8")
        return {"VersionId": "v1"}


class CutoffSelection(unittest.TestCase):
    def test_full_load_has_no_cutoff(self):
        cutoff, reason = watermark.resolve_cutoff(mapping_id="m", load_mode="FULL", changed_after=None, watermark=None)
        self.assertIsNone(cutoff)
        self.assertIn("FULL", reason)

    def test_full_load_ignores_watermark(self):
        cutoff, _ = watermark.resolve_cutoff(
            mapping_id="m",
            load_mode="FULL",
            changed_after="2026-01-01T00:00:00Z",
            watermark={"maxUpdatedAt": "2026-05-05T00:00:00Z"},
        )
        self.assertIsNone(cutoff)

    def test_explicit_changed_after_wins_over_watermark(self):
        cutoff, reason = watermark.resolve_cutoff(
            mapping_id="m",
            load_mode="INCREMENTAL",
            changed_after="2026-03-01T00:00:00Z",
            watermark={"maxUpdatedAt": "2026-07-01T00:00:00Z"},
        )
        self.assertEqual(cutoff, "2026-03-01T00:00:00Z")
        self.assertIn("configured changedAfter", reason)

    def test_watermark_is_used_with_overlap_buffer(self):
        cutoff, reason = watermark.resolve_cutoff(
            mapping_id="m",
            load_mode="INCREMENTAL",
            changed_after=None,
            watermark={
                "maxUpdatedAt": "2026-07-01T12:00:00Z",
                "lastLoadedAt": "2026-07-01T12:05:00Z",
                "lastRunId": "run-7",
            },
            overlap_seconds=300,
        )
        # 5 minutes before the recorded boundary, so records updated during the last run are
        # re-read rather than missed.
        self.assertEqual(cutoff, "2026-07-01T11:55:00Z")
        self.assertIn("run-7", reason)

    def test_zero_overlap_uses_the_exact_boundary(self):
        cutoff, _ = watermark.resolve_cutoff(
            mapping_id="m",
            load_mode="INCREMENTAL",
            changed_after=None,
            watermark={"maxUpdatedAt": "2026-07-01T12:00:00Z"},
            overlap_seconds=0,
        )
        self.assertEqual(cutoff, "2026-07-01T12:00:00Z")

    def test_falls_back_to_last_loaded_at_when_no_max_updated_at(self):
        cutoff, _ = watermark.resolve_cutoff(
            mapping_id="m",
            load_mode="INCREMENTAL",
            changed_after=None,
            watermark={"lastLoadedAt": "2026-07-01T12:00:00Z"},
            overlap_seconds=0,
        )
        self.assertEqual(cutoff, "2026-07-01T12:00:00Z")

    def test_incremental_without_any_basis_raises_actionable_error(self):
        with self.assertRaises(watermark.WatermarkError) as ctx:
            watermark.resolve_cutoff(mapping_id="orders", load_mode="INCREMENTAL", changed_after=None, watermark=None)
        message = str(ctx.exception)
        self.assertIn("orders", message)
        self.assertIn("FULL load", message)
        self.assertIn("changedAfter", message)

    def test_watermark_without_timestamps_raises(self):
        with self.assertRaises(watermark.WatermarkError):
            watermark.resolve_cutoff(
                mapping_id="m", load_mode="INCREMENTAL", changed_after=None, watermark={"mappingId": "m"}
            )


class CandidateConstruction(unittest.TestCase):
    def test_uses_the_newest_observed_updated_at(self):
        candidate = watermark.build_candidate(
            mapping_id="m",
            run_id="run-1",
            extracted_at="2026-07-02T00:00:00Z",
            max_updated_at="2026-07-01T10:00:00Z",
            record_count=3,
        )
        self.assertEqual(candidate["maxUpdatedAt"], "2026-07-01T10:00:00Z")
        self.assertEqual(candidate["recordCount"], 3)
        self.assertEqual(candidate["extractRunId"], "run-1")

    def test_never_moves_backwards_when_a_run_finds_nothing_new(self):
        candidate = watermark.build_candidate(
            mapping_id="m",
            run_id="run-2",
            extracted_at="2026-07-05T00:00:00Z",
            max_updated_at=None,
            record_count=0,
            previous={"maxUpdatedAt": "2026-07-01T10:00:00Z"},
        )
        self.assertEqual(candidate["maxUpdatedAt"], "2026-07-01T10:00:00Z")

    def test_keeps_the_later_of_previous_and_observed(self):
        candidate = watermark.build_candidate(
            mapping_id="m",
            run_id="run-3",
            extracted_at="2026-07-05T00:00:00Z",
            max_updated_at="2026-06-01T00:00:00Z",
            record_count=1,
            previous={"maxUpdatedAt": "2026-07-01T00:00:00Z"},
        )
        self.assertEqual(candidate["maxUpdatedAt"], "2026-07-01T00:00:00Z")

    def test_newest_timestamp_ignores_unparsable_values(self):
        self.assertEqual(
            watermark.newest_timestamp(["not-a-date", None, "", "2026-01-02T00:00:00Z"]),
            "2026-01-02T00:00:00Z",
        )
        self.assertIsNone(watermark.newest_timestamp(["nope", None]))


class ReadWriteRoundTrip(unittest.TestCase):
    def test_missing_watermark_reads_as_none(self):
        store = S3Store(FakeS3(), "bucket")
        self.assertIsNone(watermark.read(store, "map-a"))

    def test_commit_then_read_round_trips(self):
        client = FakeS3()
        store = S3Store(client, "bucket")
        candidate = watermark.build_candidate(
            mapping_id="map-a",
            run_id="run-1",
            extracted_at="2026-07-02T00:00:00Z",
            max_updated_at="2026-07-01T10:00:00Z",
            record_count=5,
        )
        committed = watermark.commit(
            candidate, run_id="run-1", attempt_id="a1", loaded_at="2026-07-02T00:05:00Z", loaded_record_count=5
        )
        key = watermark.write(store, "map-a", committed)
        self.assertEqual(key, "state/watermarks/mapping=map-a.json")
        stored = watermark.read(store, "map-a")
        self.assertEqual(stored["maxUpdatedAt"], "2026-07-01T10:00:00Z")
        self.assertEqual(stored["lastRunId"], "run-1")
        self.assertEqual(stored["lastLoadedRecordCount"], 5)
        # The committed watermark feeds the next run's cutoff.
        cutoff, _ = watermark.resolve_cutoff(
            mapping_id="map-a", load_mode="INCREMENTAL", changed_after=None, watermark=stored, overlap_seconds=0
        )
        self.assertEqual(cutoff, "2026-07-01T10:00:00Z")

    def test_key_is_sanitized(self):
        self.assertEqual(watermark.watermark_key("a/b c"), "state/watermarks/mapping=a-b-c.json")

    def test_state_prefix_is_outside_run_data(self):
        # runs/ and reports/ carry lifecycle expiry; watermarks must not.
        self.assertTrue(watermark.watermark_key("m").startswith("state/"))


class IncrementalFlowAcrossRuns(unittest.TestCase):
    """The agreed flow: a FULL run establishes the watermark, later runs resume from it."""

    def test_full_then_incremental_then_incremental_with_nothing_new(self):
        from migration_common.jobs.transform_load import _commit_watermarks

        client = FakeS3()
        store = S3Store(client, "bucket")

        # --- Run 1: FULL. No cutoff; sees records updated up to 2026-07-01T10:00:00Z.
        cutoff, reason = watermark.resolve_cutoff(
            mapping_id="map-a", load_mode="FULL", changed_after=None, watermark=watermark.read(store, "map-a")
        )
        self.assertIsNone(cutoff)
        candidate = watermark.build_candidate(
            mapping_id="map-a",
            run_id="run-1",
            extracted_at="2026-07-01T10:05:00Z",
            max_updated_at="2026-07-01T10:00:00Z",
            record_count=100,
            previous=None,
        )
        _commit_watermarks(
            store,
            {"registries": [{"mappingId": "map-a", "candidateWatermark": candidate}]},
            {"map-a": {"created": 100, "updated": 0, "existing": 0, "failed": 0}},
            run_id="run-1",
            attempt_id="a1",
            dry_run=False,
        )

        # --- Run 2: INCREMENTAL with no changedAfter. Resumes from the saved watermark.
        saved = watermark.read(store, "map-a")
        self.assertEqual(saved["maxUpdatedAt"], "2026-07-01T10:00:00Z")
        cutoff, reason = watermark.resolve_cutoff(
            mapping_id="map-a", load_mode="INCREMENTAL", changed_after=None, watermark=saved, overlap_seconds=300
        )
        self.assertEqual(cutoff, "2026-07-01T09:55:00Z")
        self.assertIn("saved watermark", reason)
        candidate = watermark.build_candidate(
            mapping_id="map-a",
            run_id="run-2",
            extracted_at="2026-07-08T00:00:00Z",
            max_updated_at="2026-07-07T23:00:00Z",
            record_count=5,
            previous=saved,
        )
        _commit_watermarks(
            store,
            {"registries": [{"mappingId": "map-a", "candidateWatermark": candidate}]},
            {"map-a": {"created": 5, "updated": 0, "existing": 0, "failed": 0}},
            run_id="run-2",
            attempt_id="a1",
            dry_run=False,
        )
        saved = watermark.read(store, "map-a")
        self.assertEqual(saved["maxUpdatedAt"], "2026-07-07T23:00:00Z")
        self.assertEqual(saved["lastRunId"], "run-2")

        # --- Run 3: INCREMENTAL that finds nothing new. The watermark must hold, not regress.
        cutoff, _ = watermark.resolve_cutoff(
            mapping_id="map-a", load_mode="INCREMENTAL", changed_after=None, watermark=saved, overlap_seconds=0
        )
        self.assertEqual(cutoff, "2026-07-07T23:00:00Z")
        candidate = watermark.build_candidate(
            mapping_id="map-a",
            run_id="run-3",
            extracted_at="2026-07-15T00:00:00Z",
            max_updated_at=None,
            record_count=0,
            previous=saved,
        )
        _commit_watermarks(
            store,
            {"registries": [{"mappingId": "map-a", "candidateWatermark": candidate}]},
            {"map-a": {"created": 0, "updated": 0, "existing": 0, "failed": 0}},
            run_id="run-3",
            attempt_id="a1",
            dry_run=False,
        )
        self.assertEqual(watermark.read(store, "map-a")["maxUpdatedAt"], "2026-07-07T23:00:00Z")

    def test_failed_load_keeps_the_previous_watermark_for_a_retry(self):
        from migration_common.jobs.transform_load import _commit_watermarks

        client = FakeS3(
            {
                "state/watermarks/mapping=map-a.json": json.dumps(
                    {"schemaVersion": 1, "mappingId": "map-a", "maxUpdatedAt": "2026-07-01T10:00:00Z"}
                )
            }
        )
        store = S3Store(client, "bucket")
        candidate = watermark.build_candidate(
            mapping_id="map-a",
            run_id="run-2",
            extracted_at="2026-07-08T00:00:00Z",
            max_updated_at="2026-07-07T23:00:00Z",
            record_count=10,
            previous=watermark.read(store, "map-a"),
        )
        _commit_watermarks(
            store,
            {"registries": [{"mappingId": "map-a", "candidateWatermark": candidate}]},
            {"map-a": {"created": 8, "updated": 0, "existing": 0, "failed": 2}},
            run_id="run-2",
            attempt_id="a1",
            dry_run=False,
        )
        # Unchanged, so the next incremental run re-reads the window containing the 2 failures.
        self.assertEqual(watermark.read(store, "map-a")["maxUpdatedAt"], "2026-07-01T10:00:00Z")


class CommitRules(unittest.TestCase):
    """The load stage decides whether a candidate becomes the saved watermark."""

    def _run(self, *, dry_run: bool, failed: int, candidate=True):
        from migration_common.jobs.transform_load import _commit_watermarks

        client = FakeS3()
        store = S3Store(client, "bucket")
        entry = {
            "mappingId": "map-a",
            "candidateWatermark": watermark.build_candidate(
                mapping_id="map-a",
                run_id="run-1",
                extracted_at="2026-07-02T00:00:00Z",
                max_updated_at="2026-07-01T10:00:00Z",
                record_count=4,
            )
            if candidate
            else None,
        }
        summaries = {"map-a": {"mappingId": "map-a", "created": 4, "updated": 0, "existing": 0, "failed": failed}}
        _commit_watermarks(
            store,
            {"registries": [entry]},
            summaries,
            run_id="run-1",
            attempt_id="attempt-1",
            dry_run=dry_run,
        )
        return summaries["map-a"], client

    def test_successful_live_load_commits(self):
        summary, client = self._run(dry_run=False, failed=0)
        self.assertTrue(summary["watermarkCommitted"])
        self.assertEqual(summary["watermark"]["maxUpdatedAt"], "2026-07-01T10:00:00Z")
        self.assertEqual(summary["watermark"]["lastLoadedRecordCount"], 4)
        self.assertIn("state/watermarks/mapping=map-a.json", summary["watermarkArtifact"])
        self.assertEqual(len(client.puts), 1)

    def test_dry_run_never_commits(self):
        summary, client = self._run(dry_run=True, failed=0)
        self.assertFalse(summary["watermarkCommitted"])
        self.assertIn("dry run", summary["watermarkSkipReason"])
        self.assertEqual(client.puts, [])

    def test_partial_failure_does_not_advance_the_watermark(self):
        summary, client = self._run(dry_run=False, failed=2)
        self.assertFalse(summary["watermarkCommitted"])
        self.assertIn("re-read", summary["watermarkSkipReason"])
        self.assertEqual(client.puts, [])

    def test_missing_candidate_is_reported_not_crashed(self):
        summary, client = self._run(dry_run=False, failed=0, candidate=False)
        self.assertFalse(summary["watermarkCommitted"])
        self.assertIn("no watermark candidate", summary["watermarkSkipReason"])
        self.assertEqual(client.puts, [])


class WhatTheIdMapRemembers(unittest.TestCase):
    """The map exists so a record renamed in Preview is recognised as the record it already is."""

    def setUp(self):
        self.s3 = FakeS3()
        self.store = S3Store(self.s3, "staging-bucket")

    def test_no_map_yet_reads_as_empty(self):
        # The first run for a mapping. Empty, not an error: there is nothing to remember yet.
        self.assertEqual(watermark.read_idmap(self.store, "map-a"), {})

    def test_a_written_map_reads_back(self):
        watermark.write_idmap(
            self.store, "map-a", {"prev-1": "new-1"}, run_id="run-1", updated_at="2026-07-01T00:00:00Z"
        )
        self.assertEqual(watermark.read_idmap(self.store, "map-a"), {"prev-1": "new-1"})

    def test_it_is_stored_outside_the_run_and_report_folders(self):
        # Run-data lifecycle expiry deletes runs/ and reports/. If the map lived there it would
        # vanish on a schedule and every rename after that would duplicate its record.
        key = watermark.idmap_key("map-a")
        self.assertTrue(key.startswith("state/"), key)
        self.assertNotIn("runs/", key)
        self.assertNotIn("reports/", key)

    def test_it_does_not_collide_with_the_watermark(self):
        self.assertNotEqual(watermark.idmap_key("map-a"), watermark.watermark_key("map-a"))

    def test_each_mapping_gets_its_own_map(self):
        self.assertNotEqual(watermark.idmap_key("map-a"), watermark.idmap_key("map-b"))

    def test_a_mapping_id_with_path_characters_cannot_escape_the_prefix(self):
        key = watermark.idmap_key("../../etc/passwd")
        self.assertTrue(key.startswith(watermark.IDMAP_PREFIX + "/"), key)
        self.assertNotIn("..", key)

    def test_merging_keeps_records_this_run_did_not_see(self):
        # Every incremental run carries a fraction of the registry. A record outside this run's
        # window was not un-migrated, so dropping it would make a later rename of it duplicate.
        merged = watermark.merge_idmap({"prev-1": "new-1", "prev-2": "new-2"}, {"prev-3": "new-3"})
        self.assertEqual(merged, {"prev-1": "new-1", "prev-2": "new-2", "prev-3": "new-3"})

    def test_merging_lets_this_run_correct_an_older_entry(self):
        merged = watermark.merge_idmap({"prev-1": "preview-registry"}, {"prev-1": "new-registry"})
        self.assertEqual(merged, {"prev-1": "new-registry"})

    def test_merging_ignores_half_a_pair(self):
        merged = watermark.merge_idmap({}, {"prev-1": "", "": "new-1", "prev-2": "new-2"})
        self.assertEqual(merged, {"prev-2": "new-2"})

    def test_a_malformed_map_is_an_error_rather_than_an_empty_one(self):
        # Reading a corrupt map as empty would silently re-create every renamed record, which is
        # the exact failure the map exists to prevent. Fail loudly instead.
        self.s3.objects[watermark.idmap_key("map-a")] = json.dumps(["not", "an", "object"])
        with self.assertRaises(watermark.IdMapError):
            watermark.read_idmap(self.store, "map-a")

    def test_a_map_with_a_non_object_records_member_is_an_error(self):
        self.s3.objects[watermark.idmap_key("map-a")] = json.dumps({"schemaVersion": 1, "records": []})
        with self.assertRaises(watermark.IdMapError):
            watermark.read_idmap(self.store, "map-a")


if __name__ == "__main__":
    unittest.main()
