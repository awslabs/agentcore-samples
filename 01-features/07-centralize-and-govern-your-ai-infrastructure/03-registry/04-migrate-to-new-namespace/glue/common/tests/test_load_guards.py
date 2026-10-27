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
import itertools
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.jobs.transform_load import (
    TargetNameClaimPool,
    _approval_summary,
    _process_record,
    _validate_extract_manifest,
    _validate_replay_configuration,
    _verify_mapping_has_not_changed,
    plan_target_names,
)
from migration_common.registry_api import (
    LoadResult,
    RegistryApiError,
    TargetNameClaims,
    disambiguated_target_name,
)
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


class DuplicateNameHandling(unittest.TestCase):
    """``transform.duplicateNames``: two source records, one target ``(name, recordVersion)``.

    A migrated record keeps the name its source record has and the target dedup key is
    ``(name, recordVersion)``, neither part of which Preview required to be unique -- so this is
    reachable with real data rather than hypothetical.

    ``fail`` (the default) refuses the second record to claim the identity, which is what stops one
    record from overwriting the other. ``suffix`` migrates it under the original name plus a digest
    of *that record's own source identity*, which is the part that matters: nothing in the name
    depends on the run, the batch position or a counter, so every attempt produces the same name and
    a re-run recognises the record it already migrated instead of creating a second one.
    """

    # The canonical claimant id the loader derives for the colliding record below --
    # account/region/registry/recordId -- which is the only thing the suffix is computed from.
    SECOND_CLAIMANT = f"{SOURCE['accountId']}/{SOURCE['region']}/{SOURCE['registryId']}/rec-2"

    def setUp(self):
        self.transformer = RecordTransformer({})

    def _staged(self, record_id: str, *, name: str):
        return envelope(oldRecordId=record_id, record=dict(PREVIEW_RECORD, recordId=record_id, name=name))

    def _claim(self, pool, sequence: int, record_id: str, *, name: str = "payments-mcp"):
        """Run one staged record through the worker, claiming its identity in ``sequence`` order.

        A dry run is enough: the claim is applied on every path, precisely so that a dry run cannot
        pass a batch a live load would refuse.
        """
        return _process_record(
            "runs/raw/part-00000.jsonl",
            self._staged(record_id, name=name),
            mapping_by_id={"map-a": MAPPING},
            transformer=self.transformer,
            clients=None,
            dry_run=True,
            name_claims=pool,
            claim_sequence=sequence,
        )

    def _expected_suffixed(self, name: str, claimant: str | None = None) -> str:
        digest = hashlib.sha256((claimant or self.SECOND_CLAIMANT).encode("utf-8")).hexdigest()[:8]
        return f"{name[: 255 - len(digest) - 1]}-{digest}"

    def test_the_default_refuses_the_second_record_and_says_why(self):
        pool = TargetNameClaimPool()
        first = self._claim(pool, 0, "rec-1")
        second = self._claim(pool, 1, "rec-2")

        self.assertTrue(first.succeeded)
        self.assertEqual(first.name, "payments-mcp")
        self.assertFalse(second.succeeded)
        self.assertIn("already claimed", second.error)
        self.assertIn("rec-2", second.error)

    def test_suffix_migrates_the_second_record_under_a_deterministic_distinct_name(self):
        pool = TargetNameClaimPool(duplicate_names="suffix")
        first = self._claim(pool, 0, "rec-1")
        second = self._claim(pool, 1, "rec-2")

        # The record that got there first is untouched: only the later claimant moves.
        self.assertTrue(first.succeeded)
        self.assertEqual(first.name, "payments-mcp")

        self.assertTrue(second.succeeded)
        self.assertEqual(second.name, self._expected_suffixed("payments-mcp"))
        self.assertEqual(second.transformed_record["name"], second.name)
        # Only the dedup key moved. The record is still labelled with the name its source record
        # has, in the payload and in what the crosswalk reports.
        self.assertEqual(second.display_name, "payments-mcp")
        self.assertEqual(second.transformed_record["displayName"], "payments-mcp")
        self.assertEqual(second.preview_name, "payments-mcp")
        self.assertTrue(
            any("duplicateNames" in warning and second.name in warning for warning in second.warnings),
            second.warnings,
        )

    def test_the_suffixed_name_does_not_depend_on_the_attempt(self):
        """A second attempt must reach the same name, or it would migrate the record twice."""
        names = []
        for _attempt in range(2):
            pool = TargetNameClaimPool(duplicate_names="suffix")
            self._claim(pool, 0, "rec-1")
            names.append(self._claim(pool, 1, "rec-2").name)
        self.assertEqual(names[0], names[1])
        self.assertEqual(names[0], self._expected_suffixed("payments-mcp"))

    def test_re_claiming_within_one_attempt_returns_the_same_suffixed_name(self):
        """A retried record must not walk to a new name each time it is processed."""
        pool = TargetNameClaimPool(duplicate_names="suffix")
        self._claim(pool, 0, "rec-1")
        first_pass = self._claim(pool, 1, "rec-2")
        second_pass = self._claim(pool, 2, "rec-2")
        self.assertEqual(second_pass.name, first_pass.name)

    def test_a_maximum_length_name_stays_within_the_target_bound(self):
        """Preview allows a 255-character name, so the suffix has to fit inside that, not past it."""
        long_name = "n" * 255
        pool = TargetNameClaimPool(duplicate_names="suffix")
        self._claim(pool, 0, "rec-1", name=long_name)
        second = self._claim(pool, 1, "rec-2", name=long_name)

        self.assertTrue(second.succeeded, second.error)
        self.assertEqual(len(second.name), 255)
        self.assertEqual(second.name, self._expected_suffixed(long_name))

    def test_a_distinct_record_version_is_not_a_collision_in_either_mode(self):
        """The dedup key is (name, recordVersion): same name, different version, nothing to resolve."""
        for mode in ("fail", "suffix"):
            with self.subTest(duplicateNames=mode):
                pool = TargetNameClaimPool(duplicate_names=mode)
                staged = self._staged("rec-1", name="payments-mcp")
                staged["record"]["recordVersion"] = "1.0"
                first = _process_record(
                    "runs/raw/part-00000.jsonl",
                    staged,
                    mapping_by_id={"map-a": MAPPING},
                    transformer=self.transformer,
                    clients=None,
                    dry_run=True,
                    name_claims=pool,
                    claim_sequence=0,
                )
                staged = self._staged("rec-2", name="payments-mcp")
                staged["record"]["recordVersion"] = "2.0"
                second = _process_record(
                    "runs/raw/part-00000.jsonl",
                    staged,
                    mapping_by_id={"map-a": MAPPING},
                    transformer=self.transformer,
                    clients=None,
                    dry_run=True,
                    name_claims=pool,
                    claim_sequence=1,
                )
                self.assertTrue(second.succeeded, second.error)
                self.assertEqual([first.name, second.name], ["payments-mcp", "payments-mcp"])


class _StagedRecords:
    """Just enough of ``S3Store`` for ``plan_target_names``: staged envelopes, in a chosen order.

    The order is the point: it is what a real extract does not guarantee (Preview List pagination is
    not ordered) and what the plan must therefore not depend on.
    """

    def __init__(self, envelopes):
        self._envelopes = list(envelopes)

    def iter_json_lines_objects(self, objects, *, read_ahead=0):
        # `objects` and `read_ahead` are part of the S3Store signature the caller uses; the staged
        # records are held in memory here, so neither is needed to produce them.
        for index, value in enumerate(self._envelopes):
            yield f"runs/raw/part-{index:05d}.jsonl", value


class WhichRecordKeepsASharedName(unittest.TestCase):
    """``plan_target_names``: who is entitled to a target identity, decided before the load starts.

    With ``duplicateNames = "suffix"`` the loader has to answer "which of these records keeps the name
    they share" -- and answer it the same way in every run, or a re-run renames records that are
    already in the target registry and referenced by name. The two things that answer it are committed
    state (a record already in the registry keeps the name it is there under, including records this
    run does not stage) and, for records not yet migrated, the lowest canonical claimant id. Neither
    can be influenced by staged order or by which subset of a registry a run carries, which is what
    these tests hold it to.
    """

    NAME = "payments-mcp"

    def setUp(self):
        self.transformer = RecordTransformer({})

    @staticmethod
    def _claimant(record_id: str) -> str:
        return f"{SOURCE['accountId']}/{SOURCE['region']}/{SOURCE['registryId']}/{record_id}"

    def _staged(self, record_id: str, *, name: str | None = None):
        return envelope(
            oldRecordId=record_id,
            record=dict(PREVIEW_RECORD, recordId=record_id, name=name or self.NAME),
        )

    def _key(self, name: str, version: str | None = None):
        return (str(TARGET["registryId"]), name, version)

    def _plan(self, envelopes, *, known=None, assigned=None):
        return plan_target_names(
            _StagedRecords(envelopes),
            [{"key": "runs/raw/part-00000.jsonl"}],
            mapping_by_id={"map-a": MAPPING},
            transformer=self.transformer,
            known_record_ids=known or {},
            assigned_names=assigned or {},
        )

    def _suffixed(self, record_id: str, name: str | None = None) -> str:
        return disambiguated_target_name(name or self.NAME, self._claimant(record_id))

    def test_the_lowest_source_identity_keeps_the_name(self):
        # Nothing has been migrated yet, so there is no established answer -- but there still has to be
        # a repeatable one, and the only repeatable fact about two source records is their identity.
        owners, established = self._plan([self._staged("rec-2"), self._staged("rec-1")])
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-1"))
        self.assertEqual(established, {})

    def test_the_plan_does_not_depend_on_the_order_the_records_are_staged(self):
        forwards, _ = self._plan([self._staged("rec-1"), self._staged("rec-2")])
        backwards, _ = self._plan([self._staged("rec-2"), self._staged("rec-1")])
        self.assertEqual(forwards, backwards)

    def test_a_record_already_in_the_registry_keeps_its_name_over_a_lower_identity(self):
        # rec-1 sorts lower, but rec-2 is already published under the name. Handing it to rec-1 would
        # rename a live record and leave every reference to it pointing at nothing.
        owners, established = self._plan(
            [self._staged("rec-1"), self._staged("rec-2")],
            known={"map-a": {"rec-2": "new-2"}},
            assigned={"map-a": {"rec-2": {"name": self.NAME, "recordVersion": None}}},
        )
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-2"))
        self.assertEqual(established, {self._claimant("rec-2"): self.NAME})

    def test_a_name_held_by_a_record_this_run_does_not_stage_is_not_handed_out(self):
        # An incremental run carries a fraction of the registry. The record holding this name is not in
        # this window at all, so the only thing that can protect its name is the committed state.
        owners, _ = self._plan(
            [self._staged("rec-1")],
            assigned={"map-a": {"rec-9": {"name": self.NAME, "recordVersion": None}}},
        )
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-9"))
        self.assertNotEqual(owners[self._key(self.NAME)], self._claimant("rec-1"))

    def test_a_record_that_was_moved_onto_a_suffixed_name_keeps_it_when_staged_alone(self):
        # The incremental case: only the suffixed record is in this run's window, so nothing else is
        # asking for the base name. It must still not take it -- it is in the registry under the
        # suffixed one, and taking the base name would duplicate the name it renamed away from.
        suffixed = self._suffixed("rec-2")
        owners, established = self._plan(
            [self._staged("rec-2")],
            known={"map-a": {"rec-2": "new-2"}},
            assigned={"map-a": {"rec-2": {"name": suffixed, "recordVersion": None}}},
        )
        self.assertEqual(established, {self._claimant("rec-2"): suffixed})
        self.assertEqual(owners[self._key(suffixed)], self._claimant("rec-2"))
        # It is not even a candidate for the base name: it is asking for the suffixed one, so the base
        # stays available to whichever record is entitled to it (here, one this run does not carry).
        self.assertNotIn(self._key(self.NAME), owners)

    def test_a_record_migrated_before_names_were_recorded_is_credited_with_its_own_name(self):
        # An id map written by an earlier version of this tool has no names in it. Such a record can
        # only be in the registry under its own unsuffixed name, because a collision failed the record
        # instead of renaming it -- so that is what it keeps, even against a lower identity.
        owners, established = self._plan(
            [self._staged("rec-1"), self._staged("rec-2")],
            known={"map-a": {"rec-2": "new-2"}},
        )
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-2"))
        self.assertEqual(established, {self._claimant("rec-2"): self.NAME})

    def test_a_record_renamed_at_source_releases_the_name_it_held(self):
        # The load renames that target record in place, so the name it used to hold really is free --
        # refusing to reuse it would fail a record for a collision that no longer exists.
        owners, _ = self._plan(
            [self._staged("rec-1", name="renamed-mcp"), self._staged("rec-2", name="payments-mcp")],
            known={"map-a": {"rec-1": "new-1"}},
            assigned={"map-a": {"rec-1": {"name": self.NAME, "recordVersion": None}}},
        )
        self.assertEqual(owners[self._key("renamed-mcp")], self._claimant("rec-1"))
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-2"))

    def test_the_same_name_at_a_different_record_version_is_a_separate_identity(self):
        staged_one = self._staged("rec-1")
        staged_one["record"]["recordVersion"] = "1.0"
        staged_two = self._staged("rec-2")
        staged_two["record"]["recordVersion"] = "2.0"
        owners, _ = self._plan([staged_one, staged_two])
        self.assertEqual(owners[self._key(self.NAME, "1.0")], self._claimant("rec-1"))
        self.assertEqual(owners[self._key(self.NAME, "2.0")], self._claimant("rec-2"))

    def test_the_suffixed_name_a_record_is_moved_onto_is_reserved_for_it(self):
        # The plan has to account for the names it hands out, not just the ones records asked for.
        owners, _ = self._plan([self._staged("rec-1"), self._staged("rec-2")])
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-1"))
        self.assertEqual(owners[self._key(self._suffixed("rec-2"))], self._claimant("rec-2"))

    def test_a_record_whose_own_name_is_another_records_suffixed_form_keeps_it(self):
        # Pathological but resolvable: rec-3 is *named* what rec-2 would be suffixed to. A record
        # asking for a name under its own steam outranks one being moved onto it, so rec-3 keeps it --
        # and rec-2, which now has nowhere to go, is refused at claim time rather than either of them
        # being decided by staged order.
        collides_with_suffix = self._suffixed("rec-2")
        owners, _ = self._plan(
            [
                self._staged("rec-1"),
                self._staged("rec-2"),
                self._staged("rec-3", name=collides_with_suffix),
            ]
        )
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-1"))
        self.assertEqual(owners[self._key(collides_with_suffix)], self._claimant("rec-3"))

    def test_a_record_the_transform_rejects_does_not_reserve_anything(self):
        # Its failure is reported per record by the load. Reserving a name for a record that will never
        # be written would move a healthy record off a name nothing holds.
        broken = self._staged("rec-1")
        broken["record"]["descriptors"] = "not-a-descriptor-object"
        owners, _ = self._plan([broken, self._staged("rec-2")])
        self.assertEqual(owners[self._key(self.NAME)], self._claimant("rec-2"))


class ClaimingThePlannedName(unittest.TestCase):
    """The pool applies the plan: the record entitled to a name gets it, whoever claims first."""

    NAME = "payments-mcp"

    def setUp(self):
        self.transformer = RecordTransformer({})

    @staticmethod
    def _claimant(record_id: str) -> str:
        return f"{SOURCE['accountId']}/{SOURCE['region']}/{SOURCE['registryId']}/{record_id}"

    def _claim(self, pool, sequence: int, record_id: str, name: str | None = None):
        return _process_record(
            "runs/raw/part-00000.jsonl",
            envelope(
                oldRecordId=record_id,
                record=dict(PREVIEW_RECORD, recordId=record_id, name=name or self.NAME),
            ),
            mapping_by_id={"map-a": MAPPING},
            transformer=self.transformer,
            clients=None,
            dry_run=True,
            name_claims=pool,
            claim_sequence=sequence,
        )

    def test_the_planned_owner_keeps_the_name_even_when_it_claims_second(self):
        # Without the plan this is the bug: the first record to reach the claim keeps the name, so the
        # answer changes with the staged order, and a re-extract renames both records.
        pool = TargetNameClaimPool(
            duplicate_names="suffix",
            name_owners={(str(TARGET["registryId"]), self.NAME, None): self._claimant("rec-2")},
        )
        first = self._claim(pool, 0, "rec-1")
        second = self._claim(pool, 1, "rec-2")

        self.assertEqual(first.name, disambiguated_target_name(self.NAME, self._claimant("rec-1")))
        self.assertEqual(second.name, self.NAME)

    def test_a_record_that_already_answers_to_a_suffixed_name_keeps_answering_to_it(self):
        # Nothing else is being loaded, so the name it came with is free -- and taking it would rename
        # a record that is already published under the suffixed one.
        suffixed = disambiguated_target_name(self.NAME, self._claimant("rec-2"))
        pool = TargetNameClaimPool(
            duplicate_names="suffix",
            assigned_names={self._claimant("rec-2"): suffixed},
        )
        outcome = self._claim(pool, 0, "rec-2")
        self.assertEqual(outcome.name, suffixed)
        self.assertEqual(outcome.display_name, self.NAME)

    def test_a_stale_assigned_name_that_is_not_this_records_suffix_is_ignored(self):
        # State recorded against a different base name (the record was renamed at source) must not
        # pin the record to a name it is no longer entitled to.
        pool = TargetNameClaimPool(
            duplicate_names="suffix",
            assigned_names={self._claimant("rec-2"): "something-else-entirely"},
        )
        self.assertEqual(self._claim(pool, 0, "rec-2").name, self.NAME)

    def test_the_plan_is_ignored_under_the_default_mode(self):
        # `fail` never moves a record off a name, so a plan it was handed must not change which record
        # is refused -- the default path has to behave exactly as it did before suffixing existed.
        pool = TargetNameClaimPool(
            name_owners={(str(TARGET["registryId"]), self.NAME, None): self._claimant("rec-2")},
            assigned_names={self._claimant("rec-2"): "anything"},
        )
        first = self._claim(pool, 0, "rec-1")
        second = self._claim(pool, 1, "rec-2")
        self.assertEqual(first.name, self.NAME)
        self.assertFalse(second.succeeded)
        self.assertIn("already claimed", second.error)

    def test_when_both_names_are_taken_the_error_names_both(self):
        # `suffix` reduces the collisions that stop a migration; it does not remove them. The operator
        # has to be able to tell this apart from the ordinary duplicate.
        claimant = self._claimant("rec-2")
        suffixed = disambiguated_target_name(self.NAME, claimant)
        claims = TargetNameClaims(
            {
                (str(TARGET["registryId"]), self.NAME, None): "other-record",
                (str(TARGET["registryId"]), suffixed, None): "another-record",
            },
            duplicate_names="suffix",
        )
        with self.assertRaises(RegistryApiError) as raised:
            claims.claim(str(TARGET["registryId"]), self.NAME, None, claimant)
        message = str(raised.exception)
        self.assertIn(self.NAME, message)
        self.assertIn(suffixed, message)
        self.assertIn("both names are taken", message)

    def test_a_name_that_coincides_with_another_records_suffix_resolves_the_same_in_every_order(self):
        # The one case where two records are planned onto the same identity: rec-3 is *named* what rec-2
        # would be renamed to. Reserving the planned name is not enough on its own -- the claim has to
        # enforce the reservation, or whichever of the two reached it first would keep the name and a
        # re-extract that paginated differently would rename the other one instead.
        names = {
            "rec-1": self.NAME,
            "rec-2": self.NAME,
            "rec-3": disambiguated_target_name(self.NAME, self._claimant("rec-2")),
        }
        for order in itertools.permutations(sorted(names)):
            with self.subTest(order=order):
                plan, _ = plan_target_names(
                    _StagedRecords(
                        envelope(
                            oldRecordId=record_id,
                            record=dict(PREVIEW_RECORD, recordId=record_id, name=names[record_id]),
                        )
                        for record_id in order
                    ),
                    [{"key": "runs/raw/part-00000.jsonl"}],
                    mapping_by_id={"map-a": MAPPING},
                    transformer=self.transformer,
                    known_record_ids={},
                    assigned_names={},
                )
                pool = TargetNameClaimPool(duplicate_names="suffix", name_owners=plan)
                outcomes = {
                    record_id: self._claim(pool, sequence, record_id, names[record_id])
                    for sequence, record_id in enumerate(order)
                }

                self.assertEqual(outcomes["rec-1"].name, self.NAME)
                self.assertEqual(outcomes["rec-3"].name, names["rec-3"])
                self.assertFalse(outcomes["rec-2"].succeeded)
                self.assertIn("both names are taken", outcomes["rec-2"].error)

    def test_there_is_no_suffixed_form_of_an_empty_name(self):
        # The transform never produces one; this is the guard that keeps a future caller from asking
        # the service for a name starting with '-', which it refuses.
        with self.assertRaises(RegistryApiError):
            disambiguated_target_name("", self._claimant("rec-1"))


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
