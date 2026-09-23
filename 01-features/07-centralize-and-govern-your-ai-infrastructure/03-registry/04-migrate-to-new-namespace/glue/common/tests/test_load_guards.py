"""Tests for the four guards that stand between staged data and the target registry.

These are the functions whose silent failure would corrupt a migration rather than fail it:

* ``_validate_extract_manifest``     -- staged bytes must be exactly what extraction recorded
* ``_validate_replay_configuration`` -- never live-load an extract taken under different logic
* ``_verify_mapping_has_not_changed``-- never load a record into a registry it was not read for
* ``_process_record``                -- transform + upsert one record, reporting instead of raising

Each test states the failure it prevents.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.jobs.transform_load import (
    _approval_summary,
    _process_record,
    _validate_extract_manifest,
    _validate_replay_configuration,
    _verify_mapping_has_not_changed,
)
from migration_common.registry_api import LoadResult
from migration_common.settings import replay_configuration_fingerprint
from migration_common.storage import S3Store
from migration_common.transform import RecordTransformer

RUN_ID = "run-2026-07-26-01"
SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-src"}
TARGET = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-dst"}
MAPPING = {"id": "map-a", "source": dict(SOURCE), "target": dict(TARGET)}
PREVIEW_RECORD = {
    "recordId": "rec-1",
    "name": "My MCP",
    "descriptors": {"mcp": {"server": {"inlineContent": "SERVER_JSON", "schemaVersion": "1.0"}}},
}


def envelope(**overrides):
    """A staged envelope as the extract stage writes it."""
    value = {
        "mappingId": "map-a",
        "oldRecordId": "rec-1",
        "source": dict(SOURCE),
        "target": dict(TARGET),
        "record": dict(PREVIEW_RECORD),
    }
    value.update(overrides)
    return value


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def iter_chunks(self, chunk_size: int = 1024):
        for start in range(0, len(self._data), chunk_size):
            yield self._data[start : start + chunk_size]

    def iter_lines(self):
        yield from self._data.split(b"\n")

    def read(self):
        return self._data


class FakeS3:
    """Version-addressed object store, enough for manifest reconciliation."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def stage(self, key: str, version_id: str, records: list[dict]) -> dict:
        body = ("".join(json.dumps(record) + "\n" for record in records)).encode("utf-8")
        self.objects[(key, version_id)] = body
        return {
            "key": key,
            "versionId": version_id,
            "recordCount": len(records),
            "sha256": hashlib.sha256(body).hexdigest(),
            "sizeBytes": len(body),
        }

    def get_object(self, Bucket: str, Key: str, VersionId: str = "v1"):
        return {"Body": _Body(self.objects[(Key, VersionId)])}


def manifest_for(store_meta: dict, *, run_id: str = RUN_ID) -> dict:
    return {
        "runId": run_id,
        "recordCount": store_meta["recordCount"],
        "registryCount": 1,
        "registries": [
            {
                "mappingId": "map-a",
                "status": "SUCCEEDED",
                "recordCount": store_meta["recordCount"],
                "objectCount": 1,
                "objects": [store_meta],
            }
        ],
    }


class ExtractManifestReconciliation(unittest.TestCase):
    """Prevents loading staged data that was truncated, replaced, or re-pointed."""

    def setUp(self):
        self.s3 = FakeS3()
        self.store = S3Store(self.s3, "staging")
        self.meta = self.s3.stage(
            f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-00000.jsonl",
            "v1",
            [envelope(), envelope(oldRecordId="rec-2")],
        )
        self.manifest = manifest_for(self.meta)

    def test_intact_manifest_returns_the_object_inventory(self):
        objects = _validate_extract_manifest(self.store, self.manifest, RUN_ID)
        self.assertEqual(objects, [self.meta])

    def test_run_id_mismatch_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "runId does not match"):
            _validate_extract_manifest(self.store, self.manifest, "run-other")

    def test_registries_must_be_a_list(self):
        self.manifest["registries"] = {"mappingId": "map-a"}
        with self.assertRaisesRegex(RuntimeError, "registries must be an array"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_registry_count_must_match_entries(self):
        self.manifest["registryCount"] = 2
        with self.assertRaisesRegex(RuntimeError, "registryCount does not match"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_a_failed_registry_blocks_the_load(self):
        self.manifest["registries"][0]["status"] = "FAILED"
        with self.assertRaisesRegex(RuntimeError, "must be successful"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_object_count_must_match_inventory(self):
        self.manifest["registries"][0]["objectCount"] = 3
        with self.assertRaisesRegex(RuntimeError, "objectCount does not match"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_object_outside_the_immutable_raw_prefix_is_rejected(self):
        # A manifest that points anywhere else could smuggle in records this run never extracted.
        self.manifest["registries"][0]["objects"][0]["key"] = "reports/run_id=other/raw/x.jsonl"
        with self.assertRaisesRegex(RuntimeError, "outside the immutable raw run prefix"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_duplicate_object_is_rejected(self):
        registry = self.manifest["registries"][0]
        registry["objects"] = [self.meta, dict(self.meta)]
        registry["objectCount"] = 2
        registry["recordCount"] = self.meta["recordCount"] * 2
        self.manifest["recordCount"] = registry["recordCount"]
        with self.assertRaisesRegex(RuntimeError, "appears more than once"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_every_integrity_field_is_reconciled(self):
        for field, tampered in (
            ("sha256", "0" * 64),
            ("sizeBytes", 1),
            ("recordCount", 99),
            ("versionId", "v2"),
        ):
            with self.subTest(field=field):
                manifest = manifest_for(dict(self.meta, **{field: tampered}))
                if field == "recordCount":
                    # Keep the declared totals self-consistent so the mismatch under test is
                    # the object-level one, not the roll-up.
                    manifest["recordCount"] = tampered
                    manifest["registries"][0]["recordCount"] = tampered
                if field == "versionId":
                    # A wrong versionId cannot mismatch on comparison -- reconciliation reads the
                    # exact version the manifest names -- so S3 rejects the read instead. The fake
                    # models that as a missing object; live S3 raises NoSuchVersion.
                    with self.assertRaises(KeyError):
                        _validate_extract_manifest(self.store, manifest, RUN_ID)
                    continue
                with self.assertRaisesRegex(RuntimeError, f"{field} does not match manifest"):
                    _validate_extract_manifest(self.store, manifest, RUN_ID)

    def test_registry_record_count_must_match_staged_lines(self):
        self.manifest["registries"][0]["recordCount"] = 5
        with self.assertRaisesRegex(RuntimeError, "staged record count does not match"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)

    def test_run_record_count_must_match_staged_lines(self):
        self.manifest["recordCount"] = 7
        with self.assertRaisesRegex(RuntimeError, "run record count does not match"):
            _validate_extract_manifest(self.store, self.manifest, RUN_ID)


SETTINGS = {
    "transform": {"namePrefix": "migrated", "implementationHash": "abc"},
    "api": {"target": {"serviceName": "agent-registry-control", "signingName": "agent-registry"}},
}


class ReplayConfigurationGuard(unittest.TestCase):
    """Prevents live-loading an extract that was staged under different migration logic."""

    def test_matching_fingerprint_passes(self):
        current = replay_configuration_fingerprint(SETTINGS)
        manifest = {"replayConfiguration": {"schemaVersion": 1, "sha256": current}}
        result = _validate_replay_configuration(manifest, SETTINGS, allow_drift=False)
        self.assertTrue(result["matches"])
        self.assertIsNone(result["driftReason"])
        self.assertEqual(result["expectedSha256"], current)
        self.assertEqual(result["currentSha256"], current)

    def test_changed_settings_block_the_load(self):
        manifest = {"replayConfiguration": {"schemaVersion": 1, "sha256": "0" * 64}}
        with self.assertRaisesRegex(RuntimeError, "changed after extraction"):
            _validate_replay_configuration(manifest, SETTINGS, allow_drift=False)

    def test_missing_fingerprint_blocks_the_load(self):
        with self.assertRaisesRegex(RuntimeError, "no replayConfiguration fingerprint"):
            _validate_replay_configuration({}, SETTINGS, allow_drift=False)

    def test_unsupported_schema_version_blocks_the_load(self):
        manifest = {"replayConfiguration": {"schemaVersion": 2, "sha256": "x"}}
        with self.assertRaisesRegex(RuntimeError, "unsupported replayConfiguration schemaVersion"):
            _validate_replay_configuration(manifest, SETTINGS, allow_drift=False)

    def test_blank_fingerprint_blocks_the_load(self):
        manifest = {"replayConfiguration": {"schemaVersion": 1, "sha256": ""}}
        with self.assertRaisesRegex(RuntimeError, "no sha256"):
            _validate_replay_configuration(manifest, SETTINGS, allow_drift=False)

    def test_drift_is_reported_not_raised_when_explicitly_allowed(self):
        manifest = {"replayConfiguration": {"schemaVersion": 1, "sha256": "0" * 64}}
        result = _validate_replay_configuration(manifest, SETTINGS, allow_drift=True)
        self.assertFalse(result["matches"])
        self.assertTrue(result["driftAllowed"])
        self.assertIn("changed after extraction", result["driftReason"])


class MappingDriftGuard(unittest.TestCase):
    """Prevents loading records into a registry other than the one they were extracted for."""

    def test_identical_mapping_passes(self):
        self.assertIsNone(_verify_mapping_has_not_changed(envelope(), MAPPING))

    def test_any_changed_endpoint_field_is_rejected(self):
        for side, field, value in (
            ("source", "registryId", "reg-other"),
            ("source", "accountId", "999988887777"),
            ("source", "region", "eu-west-1"),
            ("target", "registryId", "reg-other"),
            ("target", "region", "us-west-2"),
            ("target", "roleArn", "arn:aws:iam::111122223333:role/Other"),
            ("target", "externalId", "changed"),
        ):
            with self.subTest(side=side, field=field):
                current = {
                    "id": "map-a",
                    "source": dict(SOURCE),
                    "target": dict(TARGET),
                }
                current[side][field] = value
                with self.assertRaisesRegex(RuntimeError, f"{side}.{field}"):
                    _verify_mapping_has_not_changed(envelope(), current)

    def test_malformed_endpoints_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "endpoints must be objects"):
            _verify_mapping_has_not_changed(envelope(source="reg-src"), MAPPING)


class FakeTargetClient:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def upsert(
        self,
        *,
        registry_id: str,
        record: dict,
        source_record_id: str | None = None,
        known_record_id: str | None = None,
    ):
        self.calls.append(
            {
                "registryId": registry_id,
                "record": record,
                "sourceRecordId": source_record_id,
                "knownRecordId": known_record_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class FakePool:
    def __init__(self, client):
        self.client = client
        self.targets: list[dict] = []

    def for_target(self, target: dict):
        self.targets.append(target)
        return self.client


class ProcessOneRecord(unittest.TestCase):
    """The per-record worker: it must report failures as outcomes, never raise into the pool."""

    def setUp(self):
        self.transformer = RecordTransformer({})
        self.mapping_by_id = {"map-a": MAPPING}

    def _process(self, staged, *, clients=None, dry_run=False):
        return _process_record(
            "runs/raw/part-00000.jsonl",
            staged,
            mapping_by_id=self.mapping_by_id,
            transformer=self.transformer,
            clients=clients,
            dry_run=dry_run,
        )

    def test_dry_run_transforms_without_writing(self):
        client = FakeTargetClient()
        outcome = self._process(envelope(), clients=FakePool(client), dry_run=True)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.action, "dryRun")
        self.assertEqual(outcome.status, "SUCCEEDED")
        self.assertEqual(outcome.old_record_id, "rec-1")
        self.assertEqual(outcome.display_name, "My MCP")
        self.assertEqual(outcome.record_type, "MCP")
        self.assertEqual(outcome.primary_descriptor_type, "mcpServer")
        self.assertIsNone(outcome.new_record_id)
        self.assertEqual(client.calls, [], "a dry run must not call the target API")

    def test_no_client_pool_is_treated_as_a_dry_run(self):
        outcome = self._process(envelope(), clients=None, dry_run=False)
        self.assertEqual(outcome.action, "dryRun")

    def test_live_load_records_both_sides_of_the_id_mapping(self):
        described = {"recordId": "new-1", "name": "migrated-x", "status": "DRAFT"}
        pool = FakePool(FakeTargetClient(LoadResult(action="created", new_record_id="new-1", record=described)))
        outcome = self._process(envelope(), clients=pool, dry_run=False)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.action, "created")
        self.assertEqual(outcome.old_record_id, "rec-1")
        self.assertEqual(outcome.new_record_id, "new-1")
        self.assertEqual(outcome.target_record, described)
        self.assertEqual(outcome.preview_record, PREVIEW_RECORD)
        self.assertEqual(outcome.transformed_record["recordType"], "MCP")
        self.assertEqual(pool.targets, [TARGET], "record was loaded into the mapped target")
        self.assertEqual(pool.client.calls[0]["registryId"], "reg-dst")

    def test_missing_old_record_id_fails_the_record(self):
        outcome = self._process(envelope(oldRecordId="", record=dict(PREVIEW_RECORD, recordId="")))
        self.assertFalse(outcome.succeeded)
        self.assertIn("ID mapping cannot be produced", outcome.error)
        self.assertIsNotNone(outcome.traceback_text)

    def test_unknown_mapping_fails_the_record_without_raising(self):
        outcome = self._process(envelope(mappingId="map-gone"))
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.mapping_id, "map-gone")

    def test_changed_mapping_fails_the_record(self):
        outcome = self._process(envelope(target=dict(TARGET, registryId="reg-moved")))
        self.assertFalse(outcome.succeeded)
        self.assertIn("Mapping configuration changed", outcome.error)

    def test_non_object_record_fails_the_record(self):
        outcome = self._process(envelope(record="not-an-object"))
        self.assertFalse(outcome.succeeded)
        self.assertIn("must be an object", outcome.error)

    def test_untransformable_record_fails_the_record(self):
        outcome = self._process(envelope(record={"recordId": "rec-1", "name": "n"}))
        self.assertFalse(outcome.succeeded)
        self.assertIsNotNone(outcome.error)
        self.assertEqual(outcome.status, "FAILED")

    def test_target_error_is_captured_as_a_failed_outcome(self):
        pool = FakePool(FakeTargetClient(error=RuntimeError("ThrottlingException")))
        outcome = self._process(envelope(), clients=pool, dry_run=False)
        self.assertFalse(outcome.succeeded)
        self.assertIn("ThrottlingException", outcome.error)

    def test_missing_new_record_id_fails_the_record(self):
        # Without a new id the old->new crosswalk would be incomplete, so this must not pass.
        pool = FakePool(FakeTargetClient(LoadResult(action="created", new_record_id="", record={})))
        outcome = self._process(envelope(), clients=pool, dry_run=False)
        self.assertFalse(outcome.succeeded)
        self.assertIn("did not return a recordId", outcome.error)


class ApprovalSummaryReporting(unittest.TestCase):
    """The approval block is how a reviewer decides whether the migration is finished.

    Each of these cases produces `statusesApplied: 0`, and they mean completely different things, so
    the note has to tell them apart.
    """

    @staticmethod
    def _summary(**fields):
        base = {"sourceStatusCounts": {}, "targetStatusCounts": {}}
        base.update(fields)
        return {"map-a": base}

    def test_a_rerun_where_every_status_already_matches(self):
        # Nothing was applied because the records already hold their source status -- which is what a
        # second load of an already-migrated registry looks like. Reading that as "all were DRAFT"
        # would suggest nothing was ever approved.
        report = _approval_summary(
            self._summary(
                sourceStatusCounts={"APPROVED": 2, "DRAFT": 1},
                targetStatusCounts={"APPROVED": 2, "DRAFT": 1},
                statusesApplied=0,
            ),
            dry_run=False,
        )
        self.assertEqual(report["statusesApplied"], 0)
        self.assertEqual(report["recordsNeedingResubmission"], 0)
        self.assertIn("already hold that status", report["note"])

    def test_a_registry_that_was_entirely_draft_at_source(self):
        report = _approval_summary(
            self._summary(sourceStatusCounts={"DRAFT": 3}, targetStatusCounts={"DRAFT": 3}),
            dry_run=False,
        )
        self.assertIn("Every record was DRAFT", report["note"])
        self.assertEqual(report["recordsNeedingResubmission"], 0)

    def test_records_left_behind_are_counted_as_needing_attention(self):
        # Two approved at source, one still DRAFT in the target registry: that record is invisible to the data plane,
        # so it has to show up as work outstanding. `recordsStrandedInDraft` is what the load loop
        # counts per record (RecordOutcome.stranded_in_draft); the status totals alone cannot say
        # which record ended up where, which is why they are no longer what this is derived from.
        report = _approval_summary(
            self._summary(
                sourceStatusCounts={"APPROVED": 2},
                targetStatusCounts={"APPROVED": 1, "DRAFT": 1},
                statusesApplied=1,
                statusesNotApplied=1,
                recordsStrandedInDraft=1,
                statusMismatched=1,
            ),
            dry_run=False,
        )
        self.assertEqual(report["recordsNeedingResubmission"], 1)
        self.assertIn("DRAFT in the target registry", report["note"])
        self.assertIn("record-comparison/", report["note"])

    def test_an_auto_approved_record_cannot_hide_a_stranded_one(self):
        """A stranded record must be reported even when the status totals net out to zero.

        One record APPROVED at source that stayed DRAFT, and one DRAFT record the target registry
        auto-approved. The target totals then show one DRAFT and one APPROVED, exactly as the source
        totals do, so deriving this by subtracting totals reported nothing outstanding while an
        approved record sat invisible to data-plane search. This is that case.
        """
        report = _approval_summary(
            self._summary(
                sourceStatusCounts={"APPROVED": 1, "DRAFT": 1},
                targetStatusCounts={"APPROVED": 1, "DRAFT": 1},
                statusesApplied=1,
                statusesNotApplied=0,
                recordsStrandedInDraft=1,
                statusMismatched=2,
            ),
            dry_run=False,
        )
        self.assertEqual(report["recordsNeedingResubmission"], 1)
        self.assertEqual(report["statusMismatched"], 2)
        self.assertIn("DRAFT in the target registry", report["note"])

    def test_a_discoverable_mismatch_is_reported_without_alarm(self):
        """A record in a different-but-visible status is named, not folded into "all clear"."""
        report = _approval_summary(
            self._summary(
                sourceStatusCounts={"PENDING_APPROVAL": 1},
                targetStatusCounts={"APPROVED": 1},
                statusesApplied=0,
                recordsStrandedInDraft=0,
                statusMismatched=1,
            ),
            dry_run=False,
        )
        self.assertEqual(report["recordsNeedingResubmission"], 0)
        self.assertEqual(report["statusMismatched"], 1)
        self.assertIn("none are stranded in DRAFT", report["note"])


# Must stay the last statement in the file. It used to sit above ApprovalSummaryReporting, which
# meant `python test_load_guards.py` ran 30 of the 33 tests and silently skipped the three covering
# the approval block -- the one part of the report that says whether a loaded record is actually
# discoverable. Discovery (`npm test`) was unaffected, which is why it went unnoticed.
if __name__ == "__main__":
    unittest.main()
