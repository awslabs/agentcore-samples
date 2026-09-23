"""A whole migration with no AWS infrastructure: no bucket, no SSM, no deployment.

The point of this module is what it *forbids*. ``boto3`` is replaced in both job modules with an
object that raises on any attribute access, so if either stage reaches for S3 or SSM the test fails
rather than quietly working because a developer happened to have credentials. Configuration comes
from a local file with no ``adapter`` section, which is the case a user without a stack is in.

Only the two registry APIs are faked, because they are the source and target of the migration --
the thing being migrated, not infrastructure the tool needs to own.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(__file__))

from migration_common import __main__ as engine
from migration_common.jobs import extract as extract_job
from migration_common.jobs import transform_load as load_job
from test_jobs_end_to_end import FakePreviewClient, FakeTargetClient, preview_record

RUN_ID = "run-local-0001"
SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-preview"}
TARGET = {"accountId": "111122223333", "region": "us-west-2", "registryId": "reg-new"}


class _NoAws:
    """Any use of the AWS SDK for storage or configuration is a test failure."""

    def __getattr__(self, name: str):
        raise AssertionError(f"A local run must not touch AWS for infrastructure, but boto3.{name} was used")


class LocalRunNeedsNoAwsInfrastructure(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.staging = Path(self.temp.name) / "migration-run"

        # Both fakes are imported from test_jobs_end_to_end and hold all their state on the class, so
        # anything that module's last test left behind is still set. reset() clears all of it; this
        # used to clear three of the nine attributes and passed only by coincidence.
        FakePreviewClient.reset()
        FakeTargetClient.reset()
        FakePreviewClient.records = [
            preview_record(1, updated_at="2026-07-01T10:00:00Z"),
            preview_record(2, updated_at="2026-07-02T10:00:00Z"),
        ]

        for module in (extract_job, load_job):
            self._patch(module, "boto3", _NoAws())
        # Registry reachability is a real network probe against the source and target registries.
        # Those are the migration's endpoints, not infrastructure, and this module is about the
        # infrastructure being absent -- so the probes are stubbed out rather than exercised here.
        for name in ("_source_prober", "_target_prober"):
            self._patch(engine, name, lambda settings, purpose: lambda endpoint: None)
        for module in (extract_job, load_job):
            self._patch(module, "invoker_for_endpoint", lambda endpoint, run_id, purpose: "invoker")
        self._patch(extract_job, "PreviewRegistryClient", FakePreviewClient)
        self._patch(load_job, "TargetRegistryClient", FakeTargetClient)

    def _patch(self, module, name: str, value) -> None:
        original = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, original)

    def _config_file(self, *, dry_run: bool) -> str:
        """Write the deployment-shaped config a user already keeps, with no adapter section."""
        document = {
            "engine": {"account": "111122223333", "region": "us-west-2"},
            "runtime": {
                "load": {
                    "loadMode": "FULL",
                    "dryRun": dry_run,
                    "failOnRecordError": True,
                    "recordsPerObject": 500,
                    "loadConcurrency": 4,
                    "dumpExtractedRecords": True,
                },
                "transform": {"namePrefix": "migrated"},
            },
            "registries": [{"id": "map-a", "source": SOURCE, "target": TARGET}],
        }
        path = Path(self.temp.name) / f"migration-{'dry' if dry_run else 'live'}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def _arguments(self, *, dry_run: bool, run_id: str = RUN_ID) -> list[str]:
        return [
            "--config-file",
            self._config_file(dry_run=dry_run),
            "--local-dir",
            str(self.staging),
            "--RUN_ID",
            run_id,
        ]

    def _read_json(self, relative: str):
        return json.loads((self.staging / relative).read_text(encoding="utf-8"))

    def _attempt_root(self) -> Path:
        attempts = sorted((self.staging / f"reports/run_id={RUN_ID}").glob("attempt=*"))
        self.assertEqual(len(attempts), 1, "expected exactly one attempt directory")
        return attempts[0]

    def test_check_extract_and_load_all_run_from_a_directory(self):
        self.assertEqual(engine.main(["check", *self._arguments(dry_run=False)]), 0)
        extract_job.main(self._arguments(dry_run=False))
        load_job.main(self._arguments(dry_run=False))

        # Staged data and reports are files on disk, under the documented keys.
        manifest = self._read_json(f"runs/run_id={RUN_ID}/extract-manifest.json")
        self.assertEqual(manifest["status"], "SUCCEEDED")
        self.assertEqual(manifest["recordCount"], 2)
        staged = self.staging / f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-00000.jsonl"
        self.assertTrue(staged.is_file())
        self.assertEqual(len(staged.read_text(encoding="utf-8").strip().splitlines()), 2)

        extract_report = self._read_json(f"reports/run_id={RUN_ID}/extract-summary.json")
        self.assertTrue(extract_report["readyForTransform"])
        self.assertEqual(extract_report["totals"]["records"], 2)

        summary = json.loads((self._attempt_root() / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertFalse(summary["dryRun"])
        self.assertEqual(summary["registries"][0]["created"], 2)
        self.assertEqual(len(FakeTargetClient.created), 2)

        # Artifact locations are filesystem paths a reader can open, not s3:// URIs.
        for location in summary["artifacts"]:
            self.assertFalse(location.startswith("s3://"), location)
            self.assertTrue(location.startswith(str(self.staging.resolve())), location)

        crosswalk = (self._attempt_root() / "id-crosswalk/mapping=map-a.csv").read_text(encoding="utf-8")
        rows = list(csv.DictReader(crosswalk.splitlines()))
        self.assertEqual(
            [(row["oldRecordId"], row["newRecordId"]) for row in rows], [("rec-1", "new-1"), ("rec-2", "new-2")]
        )

    def test_a_dry_run_writes_reports_and_no_target_records(self):
        extract_job.main(self._arguments(dry_run=True))
        load_job.main(self._arguments(dry_run=True))

        summary = json.loads((self._attempt_root() / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["dryRun"])
        self.assertEqual(summary["registries"][0]["dryRun"], 2)
        self.assertEqual(summary["registries"][0]["created"], 0)
        self.assertEqual(FakeTargetClient.created, [])

    def test_the_replay_fingerprint_still_guards_a_local_run(self):
        """The adapter is built locally, so the guard has to work off the same document.

        Changing the transform settings after extraction must still stop a live load, or a local run
        would lose the protection a deployed run has.
        """
        extract_job.main(self._arguments(dry_run=False))

        drifted = json.loads(Path(self._config_file(dry_run=False)).read_text(encoding="utf-8"))
        drifted["runtime"]["transform"]["namePrefix"] = "changed"
        drifted_path = Path(self.temp.name) / "drifted.json"
        drifted_path.write_text(json.dumps(drifted), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "changed after extraction"):
            load_job.main(
                [
                    "--config-file",
                    str(drifted_path),
                    "--local-dir",
                    str(self.staging),
                    "--RUN_ID",
                    RUN_ID,
                ]
            )
        self.assertEqual(FakeTargetClient.created, [])

    def test_a_run_id_cannot_be_reused_locally_either(self):
        extract_job.main(self._arguments(dry_run=True))
        with self.assertRaisesRegex(RuntimeError, "Immutable local file already exists"):
            extract_job.main(self._arguments(dry_run=True))

    def test_editing_staged_records_is_caught_before_anything_loads(self):
        extract_job.main(self._arguments(dry_run=False))
        staged = self.staging / f"runs/run_id={RUN_ID}/raw/mapping=map-a/part-00000.jsonl"
        staged.write_text(staged.read_text(encoding="utf-8").replace("SERVER_1", "TAMPERED"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "reconciliation failed"):
            load_job.main(self._arguments(dry_run=False))
        self.assertEqual(FakeTargetClient.created, [])

    def test_naming_both_a_directory_and_a_bucket_is_refused(self):
        with self.assertRaisesRegex(Exception, "not both"):
            extract_job.main(self._arguments(dry_run=True) + ["--staging-bucket", "some-bucket"])

    def test_the_cli_drives_the_whole_migration_through_one_entrypoint(self):
        """What `agent-registry-migration run` does, stage by stage, with nothing deployed."""
        arguments = [
            "--config-file",
            self._config_file(dry_run=True),
            "--local-dir",
            str(self.staging),
            "--RUN_ID",
            RUN_ID,
        ]
        self.assertEqual(engine.main(["check", *arguments]), 0)
        self.assertEqual(engine.main(["extract", *arguments]), 0)
        # The CLI states its intent per run rather than storing it, so a live load of a config that
        # says dryRun=true still writes -- and only because --live said so.
        self.assertEqual(engine.main(["load", *arguments, "--live", "true"]), 0)

        summary = json.loads((self._attempt_root() / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "SUCCEEDED")
        self.assertFalse(summary["dryRun"])
        self.assertEqual(len(FakeTargetClient.created), 2)
        self.assertEqual(engine.main(["report", *arguments]), 0)

    def test_the_job_reports_its_preflight_once_and_briefly_when_it_passes(self):
        """The extract job used to render its whole pre-flight report, duplicating the CLI's.

        `agent-registry-migration extract` runs `check` as its own stage first and prints a fuller
        report -- it has the staging store and the registry probers, so it also covers reachability.
        The job then printed its own reduced one, so a live run showed "Pre-flight validation PASSED"
        twice with different check counts, and a reader could not tell which was authoritative.

        The job's own check stays, because Glue starts the job directly with no CLI in front of it.
        Only the success-path verbosity changed, so this asserts the banner is gone while the fact that
        validation ran is still recorded.
        """
        with self.assertLogs("agent-registry-migration.extract", level="INFO") as captured:
            extract_job.main(self._arguments(dry_run=False))
        messages = "\n".join(captured.output)
        self.assertNotIn("Pre-flight validation PASSED", messages)
        self.assertNotIn("[PASS]", messages)
        # Still says it validated, and how much of it, so the log is not silent about it either.
        self.assertRegex(messages, r"Pre-flight validation passed \(\d+ checks\)")

    def test_a_run_without_live_writes_nothing_however_the_file_is_configured(self):
        arguments = [
            "--config-file",
            self._config_file(dry_run=False),
            "--local-dir",
            str(self.staging),
            "--RUN_ID",
            RUN_ID,
        ]
        self.assertEqual(engine.main(["extract", *arguments]), 0)
        self.assertEqual(engine.main(["load", *arguments, "--live", "false"]), 0)

        summary = json.loads((self._attempt_root() / "summary.json").read_text(encoding="utf-8"))
        self.assertTrue(summary["dryRun"])
        self.assertEqual(FakeTargetClient.created, [])


if __name__ == "__main__":
    unittest.main()
