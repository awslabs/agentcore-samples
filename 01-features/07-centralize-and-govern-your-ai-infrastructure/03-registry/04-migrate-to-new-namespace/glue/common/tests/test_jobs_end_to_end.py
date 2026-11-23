"""End-to-end tests for both job entrypoints, wired together through one in-memory S3.

Everything is real except the two edges a laptop cannot have: the AWS SDK session and the two
registry APIs. `S3Store`, `JsonArrayWriter`, the transform, pre-flight validation, manifest
reconciliation, the replay guard, watermarks and the report writers all run for real, and the
load stage consumes exactly what the extract stage produced.

These are the tests that catch wiring regressions -- an artifact written to the wrong key, a
manifest field renamed on one side only, a report that stops being produced -- which unit tests
around individual helpers cannot see.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from botocore.exceptions import ClientError
from migration_common.jobs import extract as extract_job
from migration_common.jobs import transform_load as load_job
from migration_common.registry_api import (
    REPRODUCIBLE_STATUSES,
    LoadResult,
    RegistryApiError,
    StatusResult,
)

RUN_ID = "run-e2e-0001"
ATTEMPT_ID = "attempt-1"
BUCKET = "staging-bucket"
SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-preview"}
TARGET = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-new"}
ADAPTER = {
    "schemaVersion": 1,
    "transform": {
        "namePrefix": "migrated",
        "allowedRecordTypes": ["AGENT", "MCP", "SKILL", "CUSTOM"],
        "passthroughFields": ["description"],
        "implementationHash": "e2e-hash",
    },
    "api": {
        "preview": {
            "serviceName": "bedrock-agentcore-control",
            "signingName": "bedrock-agentcore",
            "response": {"recordTypePath": "descriptorType", "updatedAtPath": "updatedAt"},
        },
        "target": {"serviceName": "agent-registry-control", "signingName": "agent-registry"},
    },
}


def preview_record(index: int, *, updated_at: str) -> dict:
    return {
        "recordId": f"rec-{index}",
        "name": f"server-{index}",  # Preview names are [a-zA-Z0-9][a-zA-Z0-9_\-./]*
        "descriptorType": "MCP",
        "updatedAt": updated_at,
        "descriptors": {"mcp": {"server": {"inlineContent": f"SERVER_{index}", "schemaVersion": "1.0"}}},
    }


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self):
        return self._data

    def iter_lines(self):
        yield from self._data.split(b"\n")

    def iter_chunks(self, chunk_size: int = 1024):
        for start in range(0, len(self._data), chunk_size):
            yield self._data[start : start + chunk_size]


class _Events:
    """Stand-in for a botocore client's event system, tracking the If-None-Match injection."""

    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def register_first(self, event_name: str, handler, unique_id: str) -> None:
        self._client.conditional_writes += 1

    def unregister(self, event_name: str, unique_id: str) -> None:
        self._client.conditional_writes -= 1


class _Meta:
    def __init__(self, client: FakeS3Client) -> None:
        self.events = _Events(client)


class FakeS3Client:
    """Versioned in-memory S3 with enough behaviour for the real S3Store to run against."""

    def __init__(self) -> None:
        self.versions: dict[str, list[tuple[str, bytes]]] = {}
        self.conditional_writes = 0
        self.next_version = 0
        self.meta = _Meta(self)

    # -- assertions helpers -------------------------------------------------
    @property
    def keys(self) -> list[str]:
        return sorted(self.versions)

    def body(self, key: str) -> bytes:
        return self.versions[key][-1][1]

    def json(self, key: str):
        return json.loads(self.body(key).decode("utf-8"))

    def text(self, key: str) -> str:
        return self.body(key).decode("utf-8")

    def keys_under(self, prefix: str) -> list[str]:
        return sorted(key for key in self.versions if key.startswith(prefix))

    def put(self, key: str, body: bytes) -> None:
        """Write directly, bypassing the encryption assertion, to set up a test's starting state."""
        self.next_version += 1
        self.versions.setdefault(key, []).append((f"v{self.next_version}", body))

    # -- the S3 surface the store uses --------------------------------------
    def put_object(self, **kwargs):
        key = kwargs["Key"]
        if kwargs.get("ServerSideEncryption") != "AES256":
            raise AssertionError(f"unencrypted write to {key}")
        if self.conditional_writes and key in self.versions:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
                "PutObject",
            )
        self.next_version += 1
        version_id = f"v{self.next_version}"
        self.versions.setdefault(key, []).append((version_id, kwargs["Body"]))
        return {"VersionId": version_id}

    def get_object(self, Bucket: str, Key: str, VersionId: str | None = None):
        if Key not in self.versions:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "GetObject",
            )
        if VersionId is None:
            # An unversioned GET returns the *current* version, which is the newest. This used to
            # walk the list forwards and match the first entry, so it returned the OLDEST version --
            # meaning a rewritten manifest or watermark read back as its original content and any
            # test that overwrote one was silently testing the wrong bytes.
            return {"Body": _Body(self.versions[Key][-1][1])}
        for version_id, body in self.versions[Key]:
            if VersionId == version_id:
                return {"Body": _Body(body)}
        raise ClientError(
            {"Error": {"Code": "NoSuchVersion"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetObject",
        )


class FakeBoto3:
    def __init__(self, s3_client: FakeS3Client) -> None:
        self._s3 = s3_client

    def client(self, service_name: str, **_kwargs):
        if service_name != "s3":
            raise AssertionError(f"unexpected client: {service_name}")
        return self._s3


class FakePreviewClient:
    """Yields staged preview records; optionally raises to exercise the failure path."""

    records: ClassVar[list[dict]] = []
    error: Exception | None = None
    calls: ClassVar[list[dict]] = []

    @classmethod
    def reset(cls) -> None:
        """Return every piece of class state to its initial value.

        Lives on the class so a caller cannot reset a subset of it. ``test_local_run`` imports this
        fake and used to reset the attributes it happened to know about, which is a list that drifts
        every time one is added here.
        """
        cls.records = []
        cls.error = None
        cls.calls = []

    def __init__(self, invoker, api_config, region) -> None:
        self.warnings = ["preview pagination used the default page size"]

    def iter_records(self, *, registry_id: str, load_mode: str, changed_after):
        type(self).calls.append({"registryId": registry_id, "loadMode": load_mode, "changedAfter": changed_after})
        if type(self).error is not None:
            raise type(self).error
        for record in type(self).records:
            yield _Extracted(record)


class _Extracted:
    def __init__(self, record: dict) -> None:
        self.record = record
        self.old_record_id = str(record["recordId"])


class FakeTargetClient:
    """Accepts every record, or fails the ones whose displayName is in ``fail_records``.

    The new record id is derived from the record itself, not from a call counter, so the
    old->new pairing is stable no matter what order concurrent workers reach the API in. A
    counter would make this fake -- not the code under test -- the source of nondeterminism.

    ``fail_records`` fails before anything is written. ``fail_after_create`` models the other
    shape of failure -- the create returned an id and the record then failed to settle -- so the
    error carries that id the way the real client's does.
    """

    created: ClassVar[list[dict]] = []
    # Upserts that resolved onto a target record an earlier run had already recorded.
    updated: ClassVar[list[dict]] = []
    fail_records: ClassVar[set[str]] = set()
    fail_after_create: ClassVar[set[str]] = set()
    # Names on which the real client's _claim_name guard would refuse a second claimant: the first
    # source record to ask for one of these names is created normally, and any later call for the
    # same name -- from a different source record -- fails, mirroring that guard's effect without
    # reimplementing it (it is unit-tested directly against the real client in
    # test_registry_clients.py::NameCollisionGuard).
    refuse_second_claim_for: ClassVar[set[str]] = set()
    # Status transitions the fake was asked to perform, and the statuses it refuses. ``auto_approve``
    # models a target registry carrying autoApprovalRules: [APPROVE_ALL], where a submitted record
    # becomes APPROVED without a second call.
    status_calls: ClassVar[list[dict]] = []
    refuse_status: ClassVar[set[str]] = set()
    auto_approve: bool = False
    _lock = threading.Lock()
    _claimed_names: ClassVar[dict[str, str]] = {}

    @classmethod
    def reset(cls) -> None:
        """Return every piece of class state to its initial value.

        All of this fake's state is class-level, so it leaks between test modules unless every
        attribute is cleared. ``test_local_run`` imports this class and reset three of the nine,
        which left ``refuse_second_claim_for`` and ``_claimed_names`` populated by whichever test in
        ``test_jobs_end_to_end`` ran last -- and its tests passed only because the leaked claimant id
        happened to match an incoming record id. Keeping the reset list next to the attributes is
        what stops that recurring.
        """
        cls.created = []
        cls.updated = []
        cls.fail_records = set()
        cls.fail_after_create = set()
        cls.refuse_second_claim_for = set()
        cls.status_calls = []
        cls.refuse_status = set()
        cls.auto_approve = False
        cls._claimed_names = {}

    def __init__(self, invoker, api_config, region) -> None:
        self.region = region

    def upsert(
        self,
        *,
        registry_id: str,
        record: dict,
        source_record_id: str | None = None,
        known_record_id: str | None = None,
    ):
        display_name = str(record.get("displayName", ""))
        if display_name in type(self).fail_records:
            raise RuntimeError(f"ValidationException: {display_name} rejected by the target registry")
        name = str(record.get("name", ""))
        if name in type(self).refuse_second_claim_for and not known_record_id:
            with type(self)._lock:
                claimant = type(self)._claimed_names.get(name)
                if claimant is None:
                    type(self)._claimed_names[name] = source_record_id or ""
                elif claimant != source_record_id:
                    raise RuntimeError(
                        f"ValidationException: name {name!r} is already claimed by {claimant!r}, "
                        f"refusing {source_record_id!r}"
                    )
        # Mirror the real client's precedence: a target record recorded for this source record by an
        # earlier run is updated in place, whatever the record is called now.
        if known_record_id:
            with type(self)._lock:
                type(self).updated.append({"registryId": registry_id, "recordId": known_record_id, "record": record})
            return LoadResult(
                action="updated",
                new_record_id=known_record_id,
                record=dict(record, recordId=known_record_id, status="DRAFT"),
            )
        # Derived from `name`, not `displayName`: the two are the same for every fixture here except
        # a record renamed by `duplicateNames = "suffix"`, which keeps its source displayName and so
        # would otherwise be handed the id of the record it collided with.
        new_record_id = "new-" + name.rsplit("-", 1)[-1]
        if display_name in type(self).fail_after_create:
            raise RegistryApiError(
                f"target record {new_record_id} reached failure status CREATE_FAILED: Failed to fetch agent card from URL",
                record_id=new_record_id,
            )
        with type(self)._lock:
            type(self).created.append({"registryId": registry_id, "record": record})
        described = dict(record, recordId=new_record_id, status="DRAFT")
        return LoadResult(action="created", new_record_id=new_record_id, record=described)

    def apply_status(
        self,
        *,
        registry_id: str,
        record_id: str,
        desired_status: str,
        current_status: str | None = None,
        reason: str | None = None,
    ) -> StatusResult:
        """Mirror the real ladder closely enough to test what the load stage does with the outcome."""
        requested = (desired_status or "").upper()
        with type(self)._lock:
            type(self).status_calls.append({"registryId": registry_id, "recordId": record_id, "status": requested})
        result = StatusResult(requested=requested, achieved=current_status)
        if not requested or requested == "DRAFT":
            return result
        if requested not in REPRODUCIBLE_STATUSES:
            result.reproducible = False
            return result
        if requested in type(self).refuse_status:
            result.error = f"ValidationException: cannot move record to {requested}"
            result.achieved = "DRAFT"
            return result
        if requested in {"PENDING_APPROVAL", "APPROVED"}:
            result.actions.append("submitForApproval")
            if type(self).auto_approve:
                result.achieved = "APPROVED"
                return result
            if requested == "APPROVED":
                result.actions.append("updateStatus=APPROVED")
            result.achieved = requested
            return result
        result.actions.append(f"updateStatus={requested}")
        result.achieved = requested
        return result


class JobsEndToEnd(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3Client()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        # Both fakes keep all their state on the class, so it has to be cleared between tests. Done
        # through reset() rather than attribute by attribute, so the list cannot drift from the class.
        FakePreviewClient.reset()
        FakeTargetClient.reset()
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            preview_record(2, updated_at="2026-07-02T10:00:00Z"),
            preview_record(3, updated_at="2026-07-03T10:00:00Z"),
        ]

        self._patch(extract_job, "boto3", FakeBoto3(self.s3))
        self._patch(extract_job, "invoker_for_endpoint", lambda endpoint, run_id, purpose: "invoker")
        self._patch(extract_job, "PreviewRegistryClient", FakePreviewClient)
        self._patch(load_job, "boto3", FakeBoto3(self.s3))
        self._patch(load_job, "invoker_for_endpoint", lambda endpoint, run_id, purpose: "invoker")
        self._patch(load_job, "TargetRegistryClient", FakeTargetClient)

    def _patch(self, module, name: str, value):
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _config_file(self, *, transform: dict | None = None, **knobs) -> str:
        config = {
            "loadMode": "FULL",
            "changedAfter": None,
            "dryRun": True,
            "failOnRecordError": False,
            "recordsPerObject": 2,
            "loadConcurrency": 4,
            "allowReplayConfigurationDrift": False,
        }
        config.update(knobs)
        # `transform` overrides go to the adapter block, which is where the transform settings live.
        adapter = dict(ADAPTER, transform=dict(ADAPTER["transform"], **(transform or {})))
        path = os.path.join(self.temp.name, f"config-{len(os.listdir(self.temp.name))}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config": config,
                    "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
                    "adapter": adapter,
                },
                handle,
            )
        return path

    def _run_extract(self, *, run_id: str = RUN_ID, **knobs) -> None:
        extract_job.main(
            [
                "--config-file",
                self._config_file(**knobs),
                "--staging-bucket",
                BUCKET,
                "--run-id",
                run_id,
            ]
        )

    def _run_load(self, *, attempt: str = ATTEMPT_ID, run_id: str = RUN_ID, **knobs) -> None:
        load_job.main(
            [
                "--config-file",
                self._config_file(**knobs),
                "--staging-bucket",
                BUCKET,
                "--run-id",
                run_id,
                # Each attempt takes an immutable lock, so loading the same run twice needs its own.
                "--attempt-id",
                attempt,
            ]
        )

    def _comparison_rows(self) -> list[dict]:
        """Every per-record comparison row this attempt wrote, in staged order."""
        return [
            row
            for key in self.s3.keys_under(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/record-comparison/")
            for row in self.s3.json(key)
        ]

    def _failure_rows(self, mapping_id: str = "map-a") -> list[dict]:
        """Every failure row this attempt wrote for one mapping, in staged order.

        Failures are written the same way the comparison dump is -- bounded parts under a
        per-mapping prefix -- rather than accumulated and written as one object, because a failure
        row carries the full Preview and transformed payloads and a systemic problem fails every
        record in the run.
        """
        return [
            row
            for key in self.s3.keys_under(
                f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/failures/mapping={mapping_id}/"
            )
            for row in self.s3.json(key)
        ]

    # -- extract ------------------------------------------------------------
    def test_extract_writes_staging_manifests_and_report(self):
        self._run_extract()

        # Records are staged in bounded parts under the immutable raw prefix.
        raw_keys = self.s3.keys_under(f"runs/run_id={RUN_ID}/raw/mapping=map-a/")
        self.assertEqual(
            raw_keys,
            [
                f"runs/run_id={RUN_ID}/raw/mapping=map-a/_manifest.json",
                f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-00000.jsonl",
                f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-00001.jsonl",
            ],
        )
        staged = [json.loads(line) for key in raw_keys[1:] for line in self.s3.text(key).splitlines() if line.strip()]
        self.assertEqual([row["oldRecordId"] for row in staged], ["rec-1", "rec-2", "rec-3"])
        self.assertEqual(staged[0]["runId"], RUN_ID)
        self.assertEqual(staged[0]["source"], SOURCE)
        self.assertEqual(staged[0]["target"], TARGET)

        manifest = self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")
        self.assertEqual(manifest["status"], "SUCCEEDED")
        self.assertEqual(manifest["recordCount"], 3)
        self.assertEqual(manifest["registryCount"], 1)
        self.assertEqual(len(manifest["replayConfiguration"]["sha256"]), 64)
        registry = manifest["registries"][0]
        self.assertEqual(registry["objectCount"], 2)
        self.assertEqual(registry["recordTypeCounts"], {"MCP": 3})
        self.assertEqual(registry["warnings"], FakePreviewClient(None, None, None).warnings)
        for staged_object in registry["objects"]:
            self.assertEqual(len(staged_object["sha256"]), 64)
            self.assertGreater(staged_object["sizeBytes"], 0)
            self.assertTrue(staged_object["versionId"])
        # The newest source timestamp is proposed, not yet committed.
        self.assertEqual(registry["candidateWatermark"]["maxUpdatedAt"], "2026-07-03T10:00:00Z")
        self.assertIsNone(self.s3.versions.get("state/watermarks/mapping=map-a.json"))

        report = self.s3.json(f"reports/run_id={RUN_ID}/extract-summary.json")
        self.assertTrue(report["readyForTransform"])
        self.assertEqual(
            report["totals"],
            {
                "registries": 1,
                "records": 3,
                "failedRegistries": 0,
                "warnings": 1,
            },
        )
        self.assertIn(f"RUN_ID={RUN_ID}", report["nextStep"])

        # Readable dump of what was extracted, chunked like the raw staging.
        dump_keys = self.s3.keys_under(f"reports/run_id={RUN_ID}/extracted-records/")
        self.assertEqual(
            dump_keys,
            [
                f"reports/run_id={RUN_ID}/extracted-records/mapping=map-a/part-00000.json",
                f"reports/run_id={RUN_ID}/extracted-records/mapping=map-a/part-00001.json",
            ],
        )
        dumped = [row for key in dump_keys for row in self.s3.json(key)]
        self.assertEqual([row["oldRecordId"] for row in dumped], ["rec-1", "rec-2", "rec-3"])
        self.assertEqual(dumped[0]["previewRecord"], FakePreviewClient.records[0])

        # The run lock lives under state/, not in runs/ or reports/.
        self.assertIn(f"state/locks/run_id={RUN_ID}/extract.json", self.s3.keys)

    def test_both_stages_run_with_no_staging_bucket_argument(self):
        """The primary path: a deployment publishes its bucket, so commands take no arguments.

        Everything here omits --staging-bucket; the jobs must find it in the configuration and
        produce exactly the same artifacts they do when it is passed explicitly.
        """
        config_path = os.path.join(self.temp.name, "published.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config": {
                        "loadMode": "FULL",
                        "dryRun": False,
                        "failOnRecordError": True,
                        "recordsPerObject": 2,
                        "loadConcurrency": 2,
                    },
                    "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
                    "adapter": dict(ADAPTER, engine={"stagingBucket": BUCKET, "deploymentId": "default"}),
                },
                handle,
            )

        extract_job.main(["--config-file", config_path, "--run-id", RUN_ID])
        load_job.main(["--config-file", config_path, "--run-id", RUN_ID, "--attempt-id", ATTEMPT_ID])

        # Written to the published bucket, not to some default or a crash.
        self.assertEqual(self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")["recordCount"], 3)
        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertEqual(summary["registries"][0]["created"], 3)
        self.assertEqual(len(FakeTargetClient.created), 3)

    def test_a_missing_staging_bucket_is_a_clear_error(self):
        config_path = os.path.join(self.temp.name, "no-bucket.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config": {"loadMode": "FULL", "dryRun": True},
                    "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
                    "adapter": ADAPTER,  # no engine section: nothing published
                },
                handle,
            )
        with self.assertRaisesRegex(Exception, "staging bucket"):
            extract_job.main(["--config-file", config_path, "--run-id", RUN_ID])

    def test_migrated_records_end_up_in_the_status_they_hold_in_preview(self):
        """A record approved in Preview has to be approved in the target registry, not left in DRAFT.

        target creates every record in DRAFT, and a DRAFT record is not returned by data-plane search or
        the browsing APIs, so stopping at create would migrate approved records into invisibility.
        """
        FakePreviewClient.records = [
            dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="APPROVED"),
            dict(preview_record(2, updated_at="2026-07-02T10:00:00Z"), status="PENDING_APPROVAL"),
            dict(preview_record(3, updated_at="2026-07-03T10:00:00Z"), status="DRAFT"),
        ]
        self._run_extract()
        self._run_load(dryRun=False)

        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        approval = summary["approval"]
        self.assertEqual(approval["sourceStatusCounts"], {"APPROVED": 1, "PENDING_APPROVAL": 1, "DRAFT": 1})
        # Each record is now in its source's status.
        self.assertEqual(approval["targetStatusCounts"], {"APPROVED": 1, "PENDING_APPROVAL": 1, "DRAFT": 1})
        self.assertEqual(approval["statusesApplied"], 2)
        self.assertEqual(approval["statusesNotApplied"], 0)
        self.assertEqual(approval["recordsNeedingResubmission"], 0)

        # Each record's own status was requested. Sorted because records are loaded concurrently, so
        # the order these arrive in is not part of the contract.
        self.assertEqual(
            sorted(call["status"] for call in FakeTargetClient.status_calls),
            ["APPROVED", "DRAFT", "PENDING_APPROVAL"],
        )
        rows = self._comparison_rows()
        self.assertEqual(
            [(row["sourceStatus"], row["targetStatus"]) for row in rows],
            [("APPROVED", "APPROVED"), ("PENDING_APPROVAL", "PENDING_APPROVAL"), ("DRAFT", "DRAFT")],
        )
        self.assertEqual(rows[0]["statusActions"], ["submitForApproval", "updateStatus=APPROVED"])
        self.assertEqual(rows[2]["statusActions"], [])

    def test_a_registry_that_auto_approves_is_reported_not_fought(self):
        """`autoApprovalRules: [APPROVE_ALL]` on the target decides the final status.

        A record that was PENDING_APPROVAL at source becomes APPROVED, which is the target
        registry's own policy, not a migration error -- so it is reported rather than corrected.
        """
        FakeTargetClient.auto_approve = True
        FakePreviewClient.records = [
            dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="PENDING_APPROVAL")
        ]
        self._run_extract()
        self._run_load(dryRun=False)

        rows = self._comparison_rows()
        self.assertEqual(rows[0]["sourceStatus"], "PENDING_APPROVAL")
        self.assertEqual(rows[0]["targetStatus"], "APPROVED")
        self.assertTrue(
            any("approval policy decided the final state" in w for w in rows[0]["warnings"]),
            rows[0]["warnings"],
        )

    def test_a_status_the_target_refuses_is_reported_and_the_record_still_counts_as_loaded(self):
        """The record is created and correct; only the status transition failed.

        Failing the record would throw away a successful load, so the gap is reported per record and
        in the approval block instead.
        """
        FakeTargetClient.refuse_status = {"APPROVED"}
        FakePreviewClient.records = [dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="APPROVED")]
        self._run_extract()
        self._run_load(dryRun=False)

        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(summary["errorCount"], 0)
        self.assertEqual(summary["approval"]["statusesNotApplied"], 1)
        self.assertEqual(summary["approval"]["recordsNeedingResubmission"], 1)
        rows = self._comparison_rows()
        self.assertEqual(rows[0]["targetStatus"], "DRAFT")
        self.assertIn("cannot move record to APPROVED", rows[0]["statusError"])

    def test_a_source_status_that_cannot_exist_on_a_new_record_is_reported(self):
        """CREATE_FAILED describes the source record's history, not a state the service can be put into."""
        FakePreviewClient.records = [dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="CREATE_FAILED")]
        self._run_extract()
        self._run_load(dryRun=False)

        rows = self._comparison_rows()
        self.assertEqual(rows[0]["targetStatus"], "DRAFT")
        self.assertTrue(any("cannot be reproduced" in w for w in rows[0]["warnings"]), rows[0]["warnings"])
        self.assertEqual(
            self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")["approval"][
                "statusesNotApplied"
            ],
            1,
        )

    def test_status_matching_can_be_turned_off(self):
        """Off means every record lands in DRAFT, and the report says so rather than implying parity."""
        FakePreviewClient.records = [dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="APPROVED")]
        self._run_extract()
        self._run_load(dryRun=False, matchSourceStatus=False)

        self.assertEqual(FakeTargetClient.status_calls, [])
        approval = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")["approval"]
        self.assertFalse(approval["matchSourceStatus"])
        self.assertEqual(approval["recordsNeedingResubmission"], 1)
        self.assertIn("Status matching is off", approval["note"])

    def test_a_dry_run_says_what_would_happen_to_approval_state(self):
        FakePreviewClient.records = [dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), status="APPROVED")]
        self._run_extract()
        self._run_load(dryRun=True)
        approval = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")["approval"]
        self.assertEqual(approval["sourceStatusCounts"], {"APPROVED": 1})
        self.assertIn("moved to the status it holds", approval["note"])

    def test_two_source_records_sharing_a_name_stop_the_run_and_are_reported(self):
        """Two source records sharing a name are no longer caught at extraction.

        Extraction has no view of a name that will collide -- the guard that used to stop the run
        here was removed, so both records are staged, and it is the load stage's target client that
        must refuse the second one rather than let it silently overwrite the first (see
        ``NameCollisionGuard`` in test_registry_clients.py for that guard itself). What matters here
        is the job-level behaviour: extraction succeeds, and the load run does not abort on the
        first failure -- it keeps going, loads every record it can, and reports the one that failed
        alongside the ones that did not.
        """
        collide = dict(preview_record(2, updated_at="2026-07-02T10:00:00Z"), name="server-1")
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            collide,
            preview_record(3, updated_at="2026-07-03T10:00:00Z"),
        ]

        self._run_extract()
        registry = self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")["registries"][0]
        self.assertEqual(registry["status"], "SUCCEEDED")
        self.assertNotIn("duplicateNames", registry)
        report = self.s3.json(f"reports/run_id={RUN_ID}/extract-summary.json")
        self.assertTrue(report["readyForTransform"])

        # The transformed name is "server-1" for both colliding records (name is carried over
        # unchanged); the fake mirrors the real client's guard by letting the first claimant through
        # and refusing the second. loadConcurrency=1 makes "first" mean staged order, so which
        # record wins is deterministic for this test.
        FakeTargetClient.refuse_second_claim_for = {"server-1"}
        self._run_load(dryRun=False, failOnRecordError=False, loadConcurrency=1)

        load_report = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(load_report["status"], "PARTIAL_SUCCESS")
        self.assertEqual(load_report["errorCount"], 1)
        registry_summary = load_report["registries"][0]
        self.assertEqual(registry_summary["failed"], 1)
        # rec-1 (the original "server-1") and rec-3 still loaded: one failure did not stop the
        # batch. rec-2 is the record renamed to collide, so it is the one refused.
        self.assertEqual(registry_summary["created"], 2)
        self.assertEqual(
            sorted(entry["record"]["name"] for entry in FakeTargetClient.created),
            ["server-1", "server-3"],
        )

        failures = self._failure_rows()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["name"], "server-1")
        self.assertIn("already claimed", failures[0]["error"])

    def test_duplicate_names_suffix_migrates_both_records_under_distinct_names(self):
        """``transform.duplicateNames = "suffix"`` loads the second claimant instead of refusing it.

        Same two colliding source records as the test above, with the setting flipped: the run has to
        succeed with every record migrated, the second one under a distinct name derived from its own
        source identity, and its ``displayName``/``previewName`` still reading what the source record
        was called -- the rename moves the dedup key only, so the record stays recognisable in the
        crosswalk. Both stages get the same ``transform`` block because it is fingerprinted; a load
        that disagreed with its extract about this setting is refused by the replay guard.
        """
        collide = dict(preview_record(2, updated_at="2026-07-02T10:00:00Z"), name="server-1")
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            collide,
            preview_record(3, updated_at="2026-07-03T10:00:00Z"),
        ]
        suffixed = "server-1-" + hashlib.sha256(b"111122223333/us-east-1/reg-preview/rec-2").hexdigest()[:8]

        self._run_extract(transform={"duplicateNames": "suffix"})
        # The target client's own guard stays in "fail" mode, so leaving it armed proves the load
        # stage disambiguated before writing: a second claim of "server-1" would surface as an error.
        FakeTargetClient.refuse_second_claim_for = {"server-1"}
        self._run_load(dryRun=False, loadConcurrency=1, transform={"duplicateNames": "suffix"})

        load_report = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(load_report["status"], "SUCCEEDED")
        self.assertEqual(load_report["errorCount"], 0)
        self.assertEqual(load_report["registries"][0]["created"], 3)
        self.assertEqual(self._failure_rows(), [])
        self.assertEqual(
            [entry["record"]["name"] for entry in FakeTargetClient.created],
            ["server-1", suffixed, "server-3"],
        )

        # Only the dedup key moved: the renamed record still says what it was called in preview, and
        # a warning points at the name it now answers to.
        renamed = next(row for row in self._comparison_rows() if row["oldRecordId"] == "rec-2")
        self.assertEqual(renamed["name"], suffixed)
        self.assertEqual(renamed["previewName"], "server-1")
        self.assertEqual(renamed["displayName"], "server-1")
        self.assertEqual(renamed["transformedRecord"]["displayName"], "server-1")
        self.assertTrue(
            any("duplicateNames" in warning and suffixed in warning for warning in renamed["warnings"]),
            renamed["warnings"],
        )
        crosswalk = self.s3.text(
            f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/id-crosswalk/mapping=map-a.csv"
        ).splitlines()
        header = crosswalk[0].split(",")
        row = dict(zip(header, next(line for line in crosswalk[1:] if line.startswith("rec-2,")).split(",")))
        self.assertEqual(row["previewName"], "server-1")
        self.assertEqual(row["name"], suffixed)
        self.assertEqual(row["displayName"], "server-1")

        # Re-running is not a second migration: the id map matches each source record to the target
        # record it already produced, so nothing is created again and the suffixed name is unchanged.
        FakeTargetClient.created = []
        self._run_load(dryRun=False, loadConcurrency=1, transform={"duplicateNames": "suffix"}, attempt="attempt-2")
        self.assertEqual(FakeTargetClient.created, [])
        self.assertEqual(
            [entry["record"]["name"] for entry in FakeTargetClient.updated],
            ["server-1", suffixed, "server-3"],
        )
        # And the name each record answers to is committed state, not something the next run has to
        # work out again from whatever it happens to stage.
        self.assertEqual(
            self.s3.json("state/idmap/mapping=map-a.json")["names"],
            {
                "rec-1": {"name": "server-1", "recordVersion": None},
                "rec-2": {"name": suffixed, "recordVersion": None},
                "rec-3": {"name": "server-3", "recordVersion": None},
            },
        )

    def _suffix_collision_fixture(self) -> tuple[dict, str]:
        """Two source records that transform onto one name, plus the name the second must be given."""
        collide = dict(preview_record(2, updated_at="2026-07-02T10:00:00Z"), name="server-1")
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            collide,
        ]
        return collide, "server-1-" + hashlib.sha256(b"111122223333/us-east-1/reg-preview/rec-2").hexdigest()[:8]

    def _names_in(self, run_id: str, attempt: str) -> dict[str, str]:
        """``oldRecordId -> name`` from one attempt's comparison rows."""
        return {
            row["oldRecordId"]: row["name"]
            for key in self.s3.keys_under(f"reports/run_id={run_id}/attempt={attempt}/record-comparison/")
            for row in self.s3.json(key)
        }

    def test_a_suffixed_record_keeps_its_name_when_a_later_run_stages_only_it(self):
        """The record moved off a shared name keeps its name when the next run carries only it.

        The incremental case, and the one that decides whether ``suffix`` is safe to leave switched
        on: run one stages both colliding records, so one keeps ``server-1`` and the other is
        migrated as ``server-1-<hex>``. Nothing about the first record then changes, so an
        incremental window carries only the second -- and a run that worked out the name from the
        records *it* staged would find ``server-1`` unclaimed, hand it back to the record that had
        been moved off it, and rename that target record onto a name another record answers to. Two
        records would then share one name, which is the one thing the target registry does not allow,
        and the run would report SUCCEEDED.
        """
        collide, suffixed = self._suffix_collision_fixture()
        transform = {"duplicateNames": "suffix"}
        suffixed_record_id = "new-" + suffixed.rsplit("-", 1)[-1]

        self._run_extract(transform=transform)
        self._run_load(dryRun=False, loadConcurrency=1, transform=transform)
        self.assertEqual(
            [entry["record"]["name"] for entry in FakeTargetClient.created],
            ["server-1", suffixed],
        )

        FakePreviewClient.records = [dict(collide, updated_at="2026-07-05T10:00:00Z")]
        FakeTargetClient.created = []
        second_run = "20260705T000000Z-second"
        self._run_extract(run_id=second_run, transform=transform)
        self._run_load(run_id=second_run, attempt="attempt-2", dryRun=False, loadConcurrency=1, transform=transform)

        self.assertEqual(FakeTargetClient.created, [], "a record the id map already names must not be created again")
        self.assertEqual(
            [(entry["recordId"], entry["record"]["name"]) for entry in FakeTargetClient.updated],
            [(suffixed_record_id, suffixed)],
            "the record must keep the name it was migrated under, not take back the one server-1 holds",
        )
        self.assertEqual(self._names_in(second_run, "attempt-2"), {"rec-2": suffixed})
        summary = self.s3.json(f"reports/run_id={second_run}/attempt=attempt-2/summary.json")
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertEqual(summary["registries"][0]["updated"], 1)

    def test_which_record_keeps_a_shared_name_does_not_depend_on_staged_order(self):
        """Re-extracting the same registry in a different order migrates it to the same names.

        Preview list pagination does not promise a stable order, and nothing in the extract sorts
        what it stages, so two extracts of an unchanged registry can present the same records the
        other way round. Deciding the name from arrival order would then rename *both* records on the
        second run -- they would swap names -- while reporting a clean run.
        """
        _collide, suffixed = self._suffix_collision_fixture()
        transform = {"duplicateNames": "suffix"}

        self._run_extract(transform=transform)
        self._run_load(dryRun=False, loadConcurrency=1, transform=transform)
        self.assertEqual(
            [entry["record"]["name"] for entry in FakeTargetClient.created],
            ["server-1", suffixed],
        )

        FakePreviewClient.records = list(reversed(FakePreviewClient.records))
        FakeTargetClient.created = []
        second_run = "20260706T000000Z-reordered"
        self._run_extract(run_id=second_run, transform=transform)
        self._run_load(run_id=second_run, attempt="attempt-2", dryRun=False, loadConcurrency=1, transform=transform)

        self.assertEqual(FakeTargetClient.created, [])
        self.assertEqual(
            sorted((entry["recordId"], entry["record"]["name"]) for entry in FakeTargetClient.updated),
            sorted([("new-1", "server-1"), ("new-" + suffixed.rsplit("-", 1)[-1], suffixed)]),
            "re-staging the same records in another order must not swap the names they answer to",
        )
        self.assertEqual(self._names_in(second_run, "attempt-2"), {"rec-1": "server-1", "rec-2": suffixed})

    def test_a_dry_run_predicts_the_same_names_whatever_order_it_reads(self):
        """Order independence does not come from committed state: with none, the answer is the same.

        Both attempts here are dry runs against a registry nothing has migrated yet, so there is no
        id map to fall back on and the only thing left to decide the question with is the records'
        own canonical source identity -- which is what makes a dry run's prediction worth reading.
        """
        _collide, suffixed = self._suffix_collision_fixture()
        transform = {"duplicateNames": "suffix"}
        expected = {"rec-1": "server-1", "rec-2": suffixed}

        self._run_extract(transform=transform)
        self._run_load(dryRun=True, loadConcurrency=1, transform=transform)
        self.assertEqual(self._names_in(RUN_ID, ATTEMPT_ID), expected)

        FakePreviewClient.records = list(reversed(FakePreviewClient.records))
        reordered_run = "20260706T000000Z-dry-reordered"
        self._run_extract(run_id=reordered_run, transform=transform)
        self._run_load(run_id=reordered_run, attempt="attempt-2", dryRun=True, loadConcurrency=1, transform=transform)
        self.assertEqual(self._names_in(reordered_run, "attempt-2"), expected)
        self.assertIsNone(self.s3.versions.get("state/idmap/mapping=map-a.json"))

    def test_a_record_error_does_not_stop_the_run_but_can_still_fail_it(self):
        """``failOnRecordError`` only decides the run's final status, not whether it keeps going."""
        FakeTargetClient.fail_records = {"server-2"}
        self._run_extract()

        # false (the default): every record is still attempted, the run reports PARTIAL_SUCCESS,
        # and the job exits as though nothing broke -- because two of three records did load.
        self._run_load(dryRun=False, failOnRecordError=False, attempt="attempt-a")
        report_a = self.s3.json(f"reports/run_id={RUN_ID}/attempt=attempt-a/summary.json")
        self.assertEqual(report_a["status"], "PARTIAL_SUCCESS")
        self.assertEqual(len(FakeTargetClient.created), 2)

        FakeTargetClient.created = []
        FakeTargetClient.updated = []
        # true: the same batch is fully processed again (attempt-b is a separate attempt; the two
        # records that already succeeded are recognised via the id map from attempt-a and updated
        # in place rather than duplicated) but the run itself now fails on the one still-failing
        # record.
        with self.assertRaisesRegex(RuntimeError, "failed for 1 records"):
            self._run_load(dryRun=False, failOnRecordError=True, attempt="attempt-b")
        report_b = self.s3.json(f"reports/run_id={RUN_ID}/attempt=attempt-b/summary.json")
        self.assertEqual(report_b["status"], "FAILED")
        self.assertEqual(len(FakeTargetClient.created), 0)
        self.assertEqual(len(FakeTargetClient.updated), 2)

    def test_both_stages_report_progress_while_they_work(self):
        """A long stage that logs only at start and finish is indistinguishable from a hung one.

        Extraction reads one record at a time and loading is a write plus status polling per record,
        so on a large registry both are minutes of silence -- which is when someone kills a run that
        was working. The interval is patched down here; the point is that the counter is reported.
        """
        self._patch(extract_job, "PROGRESS_EVERY_RECORDS", 2)
        self._patch(load_job, "PROGRESS_EVERY_RECORDS", 2)

        with self.assertLogs("agent-registry-migration.extract", level="INFO") as extract_logs:
            self._run_extract()
        self.assertTrue(
            any("2 records extracted so far" in line for line in extract_logs.output),
            extract_logs.output,
        )

        with self.assertLogs("agent-registry-migration.transform-load", level="INFO") as load_logs:
            self._run_load(dryRun=False)
        # Reported as a position, not a bare count: the staged total comes from the extract manifest.
        self.assertTrue(
            any("Loaded 2 of 3 staged records" in line for line in load_logs.output),
            load_logs.output,
        )

    def test_a_dry_run_says_checked_rather_than_loaded(self):
        """The same line has to be honest about whether anything was written."""
        self._patch(load_job, "PROGRESS_EVERY_RECORDS", 2)
        self._run_extract()
        with self.assertLogs("agent-registry-migration.transform-load", level="INFO") as load_logs:
            self._run_load(dryRun=True)
        self.assertTrue(
            any("Checked 2 of 3 staged records" in line for line in load_logs.output),
            load_logs.output,
        )

    def test_every_other_record_keeps_the_exact_name_it_had_in_preview(self):
        """A migrated record carries the exact name it had in preview, with no rewriting."""
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            preview_record(2, updated_at="2026-07-02T10:00:00Z"),
            preview_record(3, updated_at="2026-07-03T10:00:00Z"),
        ]
        self._run_extract()
        self._run_load(dryRun=False)

        self.assertEqual(
            sorted(entry["record"]["name"] for entry in FakeTargetClient.created),
            ["server-1", "server-2", "server-3"],
        )
        for row in self._comparison_rows():
            self.assertEqual(row["name"], row["previewName"])

    def test_a_name_repeated_under_a_different_record_version_is_fine(self):
        FakePreviewClient.records = [
            dict(preview_record(1, updated_at="2026-07-01T10:00:00Z"), recordVersion="1.0"),
            dict(preview_record(2, updated_at="2026-07-02T10:00:00Z"), name="server-1", recordVersion="2.0"),
        ]
        self._run_extract()
        registry = self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")["registries"][0]
        self.assertEqual(registry["status"], "SUCCEEDED")
        report = self.s3.json(f"reports/run_id={RUN_ID}/extract-summary.json")
        self.assertTrue(report["readyForTransform"])

    def test_extract_dump_can_be_turned_off(self):
        # The staged JSONL is always written; only the readable second copy is optional.
        self._run_extract(dumpExtractedRecords=False)
        self.assertEqual(self.s3.keys_under(f"reports/run_id={RUN_ID}/extracted-records/"), [])
        self.assertEqual(len(self.s3.keys_under(f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-")), 2)
        registry = self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")["registries"][0]
        self.assertEqual(registry["extractedRecords"], [])
        self.assertIn("dumpExtractedRecords = false", registry["extractedRecordsNote"])
        self.assertEqual(registry["recordCount"], 3)

    def test_extract_run_id_cannot_be_reused(self):
        self._run_extract()
        with self.assertRaisesRegex(RuntimeError, "Immutable S3 key already exists"):
            self._run_extract()

    def test_extract_failure_is_reported_and_fails_the_job(self):
        FakePreviewClient.error = RuntimeError("AccessDeniedException: preview registry")
        with self.assertRaisesRegex(RuntimeError, "failed for 1 registry mappings"):
            self._run_extract()

        manifest = self.s3.json(f"runs/run_id={RUN_ID}/extract-manifest.json")
        self.assertEqual(manifest["status"], "FAILED")
        self.assertIn("AccessDeniedException", manifest["registries"][0]["error"])
        report = self.s3.json(f"reports/run_id={RUN_ID}/extract-summary.json")
        self.assertFalse(report["readyForTransform"])
        self.assertEqual(report["totals"]["failedRegistries"], 1)

    def test_incremental_extract_passes_the_cutoff_to_the_preview_api(self):
        self._run_extract(loadMode="INCREMENTAL", changedAfter="2026-06-01T00:00:00Z")
        self.assertEqual(FakePreviewClient.calls[-1]["changedAfter"], "2026-06-01T00:00:00Z")
        self.assertEqual(FakePreviewClient.calls[-1]["loadMode"], "INCREMENTAL")

    # -- transform/load ------------------------------------------------------
    def test_dry_run_load_reports_without_touching_the_target(self):
        self._run_extract()
        self._run_load(dryRun=True)

        report_root = f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}"
        summary = self.s3.json(f"{report_root}/summary.json")
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertTrue(summary["dryRun"])
        self.assertEqual(summary["processedRecordCount"], 3)
        self.assertEqual(summary["errorCount"], 0)
        self.assertTrue(summary["replayConfiguration"]["matches"])
        mapping_summary = summary["registries"][0]
        self.assertEqual(mapping_summary["extracted"], 3)
        self.assertEqual(mapping_summary["transformed"], 3)
        self.assertEqual(mapping_summary["dryRun"], 3)
        self.assertEqual(mapping_summary["created"], 0)
        self.assertFalse(mapping_summary["watermarkCommitted"])

        self.assertEqual(FakeTargetClient.created, [], "a dry run must not write to the target registry")
        self.assertIsNone(self.s3.versions.get("state/watermarks/mapping=map-a.json"))
        # No failures file when nothing failed.
        self.assertEqual(self.s3.keys_under(f"{report_root}/failures/"), [])

    def test_live_load_creates_records_and_commits_the_watermark(self):
        self._run_extract()
        self._run_load(dryRun=False)

        self.assertEqual([entry["registryId"] for entry in FakeTargetClient.created], ["reg-new"] * 3)
        # Workers run concurrently, so sort by the record itself rather than by arrival order.
        loaded = sorted(
            (entry["record"] for entry in FakeTargetClient.created),
            key=lambda record: record["displayName"],
        )
        self.assertEqual([record["recordType"] for record in loaded], ["MCP"] * 3)
        self.assertEqual([record["displayName"] for record in loaded], ["server-1", "server-2", "server-3"])
        self.assertEqual(list(loaded[0]["descriptors"]), ["mcpServer"])
        self.assertEqual(loaded[0]["descriptors"]["mcpServer"]["data"], "SERVER_1")

        report_root = f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}"
        summary = self.s3.json(f"{report_root}/summary.json")
        self.assertEqual(summary["status"], "SUCCEEDED")
        mapping_summary = summary["registries"][0]
        self.assertEqual(mapping_summary["created"], 3)
        self.assertEqual(mapping_summary["failed"], 0)
        self.assertTrue(mapping_summary["watermarkCommitted"])

        # Side-by-side comparison dump: preview record, transformed payload, target record.
        comparison_keys = self.s3.keys_under(f"{report_root}/record-comparison/")
        self.assertEqual(
            comparison_keys,
            [
                f"{report_root}/record-comparison/mapping=map-a/part-00000.json",
                f"{report_root}/record-comparison/mapping=map-a/part-00001.json",
            ],
        )
        rows = [row for key in comparison_keys for row in self.s3.json(key)]
        self.assertEqual([row["oldRecordId"] for row in rows], ["rec-1", "rec-2", "rec-3"])
        self.assertEqual([row["newRecordId"] for row in rows], ["new-1", "new-2", "new-3"])
        self.assertEqual(rows[0]["previewRecord"], FakePreviewClient.records[0])
        self.assertEqual(rows[0]["transformedRecord"]["displayName"], "server-1")
        self.assertEqual(rows[0]["targetRecord"]["recordId"], "new-1")

        # Customer-facing crosswalk CSV.
        crosswalk = self.s3.text(f"{report_root}/id-crosswalk/mapping=map-a.csv").splitlines()
        self.assertEqual(crosswalk[0].split(",")[:2], ["oldRecordId", "newRecordId"])
        self.assertEqual(
            [line.split(",")[:2] for line in crosswalk[1:]],
            [["rec-1", "new-1"], ["rec-2", "new-2"], ["rec-3", "new-3"]],
        )

        # Watermark is committed only now that records are in the target registry.
        watermark = self.s3.json("state/watermarks/mapping=map-a.json")
        self.assertEqual(watermark["maxUpdatedAt"], "2026-07-03T10:00:00Z")
        self.assertEqual(watermark["lastLoadedRecordCount"], 3)
        self.assertEqual(watermark["lastRunId"], RUN_ID)

        # Every artifact is named in the summary, so the report explains itself.
        for uri in summary["artifacts"]:
            self.assertTrue(uri.startswith(f"s3://{BUCKET}/reports/run_id={RUN_ID}/"))

    def test_a_record_renamed_between_runs_updates_instead_of_migrating_twice(self):
        # The incremental case that name matching cannot get right. Run one migrates rec-1 as
        # "server-1"; it is renamed in Preview; run two must recognise it by the id run one recorded
        # and update that target record, not create a second one and orphan the first.
        self._run_extract()
        self._run_load(dryRun=False)
        self.assertEqual(
            self.s3.json("state/idmap/mapping=map-a.json")["records"],
            {"rec-1": "new-1", "rec-2": "new-2", "rec-3": "new-3"},
        )

        renamed = dict(FakePreviewClient.records[0], name="server-1-renamed")
        FakePreviewClient.records = [renamed]
        second_run = "20260704T000000Z-second"
        self._run_extract(run_id=second_run)
        self._run_load(dryRun=False, run_id=second_run, attempt="attempt-2")

        self.assertEqual(
            [entry["recordId"] for entry in FakeTargetClient.updated],
            ["new-1"],
            "the renamed record must land on the target record it was already migrated to",
        )
        self.assertEqual(len(FakeTargetClient.created), 3, "no fourth target record may be created for a rename")
        summary = self.s3.json(f"reports/run_id={second_run}/attempt=attempt-2/summary.json")["registries"][0]
        self.assertEqual(summary["updated"], 1)
        self.assertEqual(summary.get("created", 0), 0)
        crosswalk = self.s3.text(
            f"reports/run_id={second_run}/attempt=attempt-2/id-crosswalk/mapping=map-a.csv"
        ).splitlines()
        self.assertEqual([line.split(",")[:2] for line in crosswalk[1:]], [["rec-1", "new-1"]])
        # The map still names the records this run's window did not include.
        self.assertEqual(
            self.s3.json("state/idmap/mapping=map-a.json")["records"],
            {"rec-1": "new-1", "rec-2": "new-2", "rec-3": "new-3"},
        )

    def test_a_dry_run_records_no_id_map(self):
        # Nothing reached the target registry, so there is nothing to remember -- and writing a map from a dry run
        # would make the next live run think those records already existed.
        self._run_extract()
        self._run_load(dryRun=True)
        self.assertIsNone(self.s3.versions.get("state/idmap/mapping=map-a.json"))
        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertFalse(summary["registries"][0]["idMapCommitted"])

    def test_records_that_reached_the_target_are_remembered_even_when_the_run_partly_failed(self):
        # Unlike the watermark, the id map must advance after a partial failure: the ids in it name
        # records that exist in the target registry, and forgetting one makes the next run create a second copy.
        FakeTargetClient.fail_records = {"server-2"}
        self._run_extract()
        self._run_load(dryRun=False, failOnRecordError=False)

        stored = self.s3.json("state/idmap/mapping=map-a.json")["records"]
        self.assertEqual(stored, {"rec-1": "new-1", "rec-3": "new-3"})
        self.assertNotIn("rec-2", stored, "the record that never reached the target registry has no id to remember")

    def test_a_record_created_then_stuck_is_still_remembered(self):
        # It failed to settle, but it is in the registry. If the next run forgot it, it would create
        # a duplicate rather than retry the one that is already there.
        FakeTargetClient.fail_after_create = {"server-2"}
        self._run_extract()
        self._run_load(dryRun=False, failOnRecordError=False)
        self.assertEqual(self.s3.json("state/idmap/mapping=map-a.json")["records"]["rec-2"], "new-2")

    def test_partial_failure_keeps_the_watermark_and_writes_a_failures_file(self):
        FakeTargetClient.fail_records = {"server-2"}
        self._run_extract()
        self._run_load(dryRun=False, failOnRecordError=False)

        report_root = f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}"
        summary = self.s3.json(f"{report_root}/summary.json")
        self.assertEqual(summary["status"], "PARTIAL_SUCCESS")
        self.assertEqual(summary["errorCount"], 1)
        self.assertEqual(summary["processedRecordCount"], 3)
        mapping_summary = summary["registries"][0]
        self.assertEqual(mapping_summary["created"], 2)
        self.assertEqual(mapping_summary["failed"], 1)
        self.assertFalse(mapping_summary["watermarkCommitted"])
        self.assertIn("re-reads them", mapping_summary["watermarkSkipReason"])
        # The watermark must not advance past records that never reached the target registry.
        self.assertIsNone(self.s3.versions.get("state/watermarks/mapping=map-a.json"))

        failures = self._failure_rows()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["oldRecordId"], "rec-2")
        self.assertIn("rejected by the target registry", failures[0]["error"])
        self.assertIsNotNone(failures[0]["traceback"])
        # Nothing was created for this record, so there is no id to report.
        self.assertIsNone(failures[0]["newRecordId"])
        crosswalk = self.s3.text(f"{report_root}/id-crosswalk/mapping=map-a.csv").splitlines()
        failed_row = next(line for line in crosswalk[1:] if line.startswith("rec-2,"))
        self.assertEqual(failed_row.split(",")[:2], ["rec-2", ""])

    def test_a_record_created_then_failed_is_named_in_the_crosswalk(self):
        """A target record can exist and still fail: created, then CREATE_FAILED while it settles.

        Reporting that as a failure with an empty newRecordId hides a record that is really there,
        so anyone cleaning up from the crosswalk would leave it behind.
        """
        FakeTargetClient.fail_after_create = {"server-2"}
        self._run_extract()
        self._run_load(dryRun=False, failOnRecordError=False)

        report_root = f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}"
        summary = self.s3.json(f"{report_root}/summary.json")
        self.assertEqual(summary["status"], "PARTIAL_SUCCESS")
        self.assertEqual(summary["registries"][0]["failed"], 1)

        # The failure names the record that was left behind, and says why it failed.
        failures = self._failure_rows()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["oldRecordId"], "rec-2")
        self.assertEqual(failures[0]["newRecordId"], "new-2")
        self.assertIn("CREATE_FAILED", failures[0]["error"])

        # And so does the crosswalk row, while still reporting the row as a failure.
        crosswalk = self.s3.text(f"{report_root}/id-crosswalk/mapping=map-a.csv").splitlines()
        header = crosswalk[0].split(",")
        row = dict(zip(header, next(line for line in crosswalk[1:] if line.startswith("rec-2,")).split(",")))
        self.assertEqual(row["newRecordId"], "new-2")
        self.assertEqual(row["action"], "failed")
        self.assertEqual(row["status"], "FAILED")

    def test_fail_on_record_error_marks_the_attempt_failed(self):
        FakeTargetClient.fail_records = {"server-3"}
        self._run_extract()
        with self.assertRaisesRegex(RuntimeError, "1 record"):
            self._run_load(dryRun=False, failOnRecordError=True)
        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(summary["status"], "FAILED")

    def test_load_refuses_an_extract_that_did_not_succeed(self):
        FakePreviewClient.error = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self._run_extract()
        with self.assertRaisesRegex(RuntimeError, "is not successful"):
            self._run_load(dryRun=True)

    def test_live_load_refuses_configuration_drift(self):
        self._run_extract()
        # Same run, different transform settings: the fingerprint no longer matches what the
        # staged records were extracted under, so a live load must refuse.
        original_prefix = ADAPTER["transform"]["namePrefix"]
        ADAPTER["transform"]["namePrefix"] = "changed"
        self.addCleanup(ADAPTER["transform"].__setitem__, "namePrefix", original_prefix)
        with self.assertRaisesRegex(RuntimeError, "changed after extraction"):
            self._run_load(dryRun=False)
        self.assertEqual(FakeTargetClient.created, [])

    def test_load_refuses_a_changed_mapping(self):
        self._run_extract()
        moved = {"id": "map-a", "source": SOURCE, "target": dict(TARGET, registryId="reg-somewhere-else")}
        path = os.path.join(self.temp.name, "moved.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "config": {
                        "loadMode": "FULL",
                        "dryRun": False,
                        "failOnRecordError": True,
                        "recordsPerObject": 2,
                        "loadConcurrency": 2,
                    },
                    "registries": [moved],
                    "adapter": ADAPTER,
                },
                handle,
            )
        with self.assertRaises(RuntimeError):
            load_job.main(
                ["--config-file", path, "--staging-bucket", BUCKET, "--run-id", RUN_ID, "--attempt-id", ATTEMPT_ID]
            )
        self.assertEqual(FakeTargetClient.created, [], "no record may be written to a moved target")

    def test_attempt_id_cannot_be_reused(self):
        self._run_extract()
        self._run_load(dryRun=True)
        with self.assertRaisesRegex(RuntimeError, "Immutable S3 key already exists"):
            self._run_load(dryRun=True)

    def _drop_one_staged_record(self) -> None:
        """Make the load stage see one fewer record than extraction staged.

        The final reconciliation compares records actually processed against the manifest's count.
        Getting there needs a manifest that is internally consistent -- an inflated count is rejected
        much earlier, by `_validate_extract_manifest` -- so the loss is injected where it would really
        occur, in the iteration over staged records.
        """
        original = load_job._iter_outcomes

        def short(staged_records, worker, *, concurrency):
            outcomes = list(original(staged_records, worker, concurrency=concurrency))
            return iter(outcomes[:-1])

        self._patch(load_job, "_iter_outcomes", short)

    def test_a_reconciliation_failure_still_writes_the_report(self):
        """A count mismatch must fail the run *and* leave a report explaining it.

        This used to raise the moment the counts disagreed, which was after the crosswalks, the
        failure rows and the id maps had been written but before summary.json -- a half-populated
        report directory with no statement of what went wrong, on the one run where the numbers are
        how you work out what was missed.
        """
        self._run_extract()
        self._drop_one_staged_record()

        with self.assertRaisesRegex(RuntimeError, "extract manifest declares"):
            self._run_load(dryRun=True)

        report_root = f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}"
        report = self.s3.json(f"{report_root}/summary.json")
        self.assertEqual(report["status"], "FAILED")
        reconciliation = report["reconciliation"]
        self.assertFalse(reconciliation["matches"])
        self.assertEqual(reconciliation["expectedRecordCount"], 3)
        self.assertEqual(reconciliation["processedRecordCount"], 2)
        self.assertIn("extract manifest declares", reconciliation["error"])
        # The page is written too, so the failure is reviewable the same way a success is.
        self.assertIn(f"{report_root}/summary.html", self.s3.keys)
        self.assertTrue(self.s3.text(f"{report_root}/summary.html").startswith("<!doctype html>"))
        # And the error names where to read the numbers, rather than just stating them.
        self.assertIn("summary.json", str(report["artifacts"]))

    def test_a_reconciliation_failure_leaves_the_watermark_alone(self):
        """An unreconciled run must not advance a watermark: the window has to be re-read."""
        self._run_extract()
        self._drop_one_staged_record()

        with self.assertRaises(RuntimeError):
            self._run_load(dryRun=False)

        self.assertIsNone(self.s3.versions.get("state/watermarks/mapping=map-a.json"))
        summary = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")["registries"][0]
        self.assertFalse(summary["watermarkCommitted"])
        self.assertIn("reconciliation failed", summary["watermarkSkipReason"])

    def test_a_reconciled_run_reports_that_it_reconciled(self):
        """The positive case, so the block above is not only ever seen when something is wrong."""
        self._run_extract()
        self._run_load(dryRun=True)
        report = self.s3.json(f"reports/run_id={RUN_ID}/attempt={ATTEMPT_ID}/summary.json")
        self.assertEqual(
            report["reconciliation"],
            {
                "expectedRecordCount": 3,
                "processedRecordCount": 3,
                "matches": True,
                "error": None,
            },
        )

    def test_results_are_identical_at_every_concurrency(self):
        """Concurrency must be invisible in the report: same rows, same order, same pairing."""
        self._run_extract()
        baselines = []
        for concurrency in (1, 2, 8):
            # Each pass writes its own attempt, so nothing needs resetting between them.
            load_job.main(
                [
                    "--config-file",
                    self._config_file(dryRun=False, loadConcurrency=concurrency),
                    "--staging-bucket",
                    BUCKET,
                    "--run-id",
                    RUN_ID,
                    "--attempt-id",
                    f"attempt-c{concurrency}",
                ]
            )
            rows = [
                row
                for key in self.s3.keys_under(
                    f"reports/run_id={RUN_ID}/attempt=attempt-c{concurrency}/record-comparison/"
                )
                for row in self.s3.json(key)
            ]
            baselines.append([(row["oldRecordId"], row["newRecordId"]) for row in rows])
        self.assertEqual(baselines[0], baselines[1])
        self.assertEqual(baselines[0], baselines[2])


if __name__ == "__main__":
    unittest.main()
