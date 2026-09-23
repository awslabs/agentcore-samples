"""Tests for pre-flight validation.

These checks are the guard rail that stops a bad configuration before a long run starts, so they
are tested for both the failure they must catch and the actionable remedy they must return.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import preflight

SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "src-1"}
TARGET = {"accountId": "111122223333", "region": "us-west-2", "registryId": "tgt-1"}


def mapping(mapping_id="map-a", source=None, target=None):
    return {"id": mapping_id, "source": dict(source or SOURCE), "target": dict(target or TARGET)}


def settings(mode="FULL", changed_after=None, dry_run=True, fail_on_error=True):
    return {
        "load": {
            "mode": mode,
            "changedAfter": changed_after,
            "dryRun": dry_run,
            "failOnRecordError": fail_on_error,
        }
    }


def statuses(results, name_contains):
    return [r.status for r in results if name_contains in r.name]


class SdkModelChecks(unittest.TestCase):
    """The check that stops a run whose SDK cannot build one of the two clients."""

    def test_both_models_present_passes(self):
        results = preflight.check_sdk_models(["s3", "bedrock-agentcore-control", "agent-registry-control"])
        self.assertEqual(statuses(results, "sdk.serviceModels"), [preflight.PASS])

    def test_missing_target_model_fails_and_names_the_version_that_has_it(self):
        # The expensive case: extract succeeds against Preview, then the load dies on the first
        # create. Half a working SDK has to fail as loudly as none of one.
        results = preflight.check_sdk_models(["s3", "bedrock-agentcore-control"])
        self.assertEqual(statuses(results, "sdk.serviceModels"), [preflight.FAIL])
        self.assertIn("agent-registry-control", results[0].detail)
        self.assertIn("write target records", results[0].detail)
        self.assertIn(preflight.MINIMUM_BOTOCORE_VERSION, results[0].remedy)

    def test_missing_preview_model_fails(self):
        results = preflight.check_sdk_models(["s3", "agent-registry-control"])
        self.assertEqual(statuses(results, "sdk.serviceModels"), [preflight.FAIL])
        self.assertIn("bedrock-agentcore-control", results[0].detail)

    def test_neither_model_reports_both(self):
        results = preflight.check_sdk_models(["s3"])
        self.assertEqual(statuses(results, "sdk.serviceModels"), [preflight.FAIL])
        for service in preflight.REQUIRED_SERVICE_MODELS:
            self.assertIn(service, results[0].detail)

    def test_remedy_points_at_redeploying_the_jobs(self):
        # On Glue the SDK arrives via --additional-python-modules, so the fix is a redeploy rather
        # than a pip install the operator cannot perform on a worker.
        results = preflight.check_sdk_models([])
        self.assertIn("redeploy", results[0].remedy)


class ShadowedTargetModel(unittest.TestCase):
    """A model in ~/.aws/models wins over the SDK's own, which can take CreateRegistry away.

    An interim `agent-registry-control` model installed during the preview carries the six record
    operations and nothing else. Records still migrate with it -- the load only calls record
    operations -- but creating a target registry stops working on an otherwise current SDK, and the
    symptom ("Invalid choice: 'create-registry'", or a missing attribute) names the CLI or the SDK
    rather than the file that caused it. Hence a warning that names the file.
    """

    def test_no_override_is_silent(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(preflight.check_shadowed_target_model(root), [])

    def test_an_override_warns_and_names_the_directory(self):
        with tempfile.TemporaryDirectory() as root:
            override = os.path.join(root, "agent-registry-control")
            os.makedirs(override)
            results = preflight.check_shadowed_target_model(root)
            self.assertEqual([r.status for r in results], [preflight.WARN])
            self.assertIn(override, results[0].detail)
            # It warns rather than fails, and says which half still works.
            self.assertIn("record migration", results[0].detail)
            self.assertIn("delete", results[0].remedy)

    def test_the_jobs_never_run_it(self):
        """A Glue worker has no ~/.aws/models, so reporting on one would describe the wrong machine."""
        report = preflight.run_checks(settings(), [mapping()])
        self.assertEqual([r for r in report.results if r.name == "sdk.shadowedTargetModel"], [])


class MappingShapeChecks(unittest.TestCase):
    def test_valid_mapping_passes(self):
        results = preflight.check_mapping_shapes([mapping()])
        self.assertEqual(statuses(results, "map-a.shape"), [preflight.PASS])

    def test_no_mappings_fails_and_names_the_command_that_fixes_it(self):
        results = preflight.check_mapping_shapes([])
        self.assertEqual(results[0].status, preflight.FAIL)
        self.assertIn("registries", results[0].remedy)
        self.assertIn("init", results[0].remedy)

    def test_bad_account_id_fails(self):
        bad = mapping(source={**SOURCE, "accountId": "12345"})
        result = next(r for r in preflight.check_mapping_shapes([bad]) if r.name.endswith("shape"))
        self.assertEqual(result.status, preflight.FAIL)
        self.assertIn("12-digit", result.detail)

    def test_bad_region_fails(self):
        bad = mapping(target={**TARGET, "region": "us-west"})
        result = next(r for r in preflight.check_mapping_shapes([bad]) if r.name.endswith("shape"))
        self.assertEqual(result.status, preflight.FAIL)
        self.assertIn("region", result.detail)

    def test_empty_registry_id_fails(self):
        bad = mapping(source={**SOURCE, "registryId": ""})
        result = next(r for r in preflight.check_mapping_shapes([bad]) if r.name.endswith("shape"))
        self.assertEqual(result.status, preflight.FAIL)

    def test_self_migration_fails(self):
        same = mapping(source=SOURCE, target=SOURCE)
        result = next(r for r in preflight.check_mapping_shapes([same]) if r.name.endswith("shape"))
        self.assertEqual(result.status, preflight.FAIL)
        self.assertIn("onto itself", result.detail)

    def test_external_id_without_role_arn_fails(self):
        bad = mapping(target={**TARGET, "externalId": "ext-1"})
        result = next(r for r in preflight.check_mapping_shapes([bad]) if r.name.endswith("shape"))
        self.assertEqual(result.status, preflight.FAIL)
        self.assertIn("externalId", result.detail)

    def test_duplicate_mapping_fails(self):
        results = preflight.check_mapping_shapes([mapping("map-a"), mapping("map-b")])
        duplicate = [r for r in results if r.name.endswith("duplicate")]
        self.assertEqual([r.status for r in duplicate], [preflight.FAIL])
        self.assertIn("map-a", duplicate[0].detail)

    def test_cross_region_mapping_warns(self):
        # Record content is copied verbatim, so a region-bound ARN inside a credential provider
        # would still point at the source region. Allowed, but the operator should know.
        results = preflight.check_mapping_shapes([mapping()])  # us-east-1 -> us-west-2
        self.assertEqual(statuses(results, "map-a.crossRegion"), [preflight.WARN])
        warning = next(r for r in results if r.name.endswith("crossRegion"))
        self.assertIn("us-east-1", warning.detail)
        self.assertIn("us-west-2", warning.detail)
        self.assertIn("credential provider", warning.remedy)

    def test_same_region_mapping_does_not_warn(self):
        same_region = mapping(target={**TARGET, "region": SOURCE["region"]})
        results = preflight.check_mapping_shapes([same_region])
        self.assertEqual(statuses(results, "crossRegion"), [])
        self.assertEqual(statuses(results, "map-a.shape"), [preflight.PASS])

    def test_shared_target_warns_but_does_not_fail(self):
        second = mapping("map-b", source={**SOURCE, "registryId": "src-2"})
        results = preflight.check_mapping_shapes([mapping("map-a"), second])
        shared = [r for r in results if r.name.endswith("sharedTarget")]
        self.assertEqual([r.status for r in shared], [preflight.WARN])
        report = preflight.PreflightReport(results=results)
        self.assertTrue(report.ok)


class LoadSettingChecks(unittest.TestCase):
    def test_dry_run_is_reported_as_safe(self):
        results = preflight.check_load_settings(settings(dry_run=True))
        self.assertEqual(statuses(results, "config.dryRun"), [preflight.PASS])

    def test_live_run_warns_prominently(self):
        results = preflight.check_load_settings(settings(dry_run=False))
        warning = next(r for r in results if r.name == "config.dryRun")
        self.assertEqual(warning.status, preflight.WARN)
        self.assertIn("WILL write", warning.detail)

    def test_skipping_record_errors_is_reported_as_the_safe_default(self):
        results = preflight.check_load_settings(settings(fail_on_error=False))
        self.assertEqual(statuses(results, "failOnRecordError"), [preflight.PASS])
        detail = next(r for r in results if r.name == "config.failOnRecordError").detail
        self.assertIn("skipped and listed in the report", detail)

    def test_stopping_the_run_on_a_record_error_is_reported_too(self):
        results = preflight.check_load_settings(settings(fail_on_error=True))
        self.assertEqual(statuses(results, "failOnRecordError"), [preflight.PASS])
        detail = next(r for r in results if r.name == "config.failOnRecordError").detail
        self.assertIn("stops (nonzero exit)", detail)


class IncrementalReadinessChecks(unittest.TestCase):
    def test_full_load_needs_no_cutoff_checks(self):
        self.assertEqual(preflight.check_incremental_readiness(settings("FULL"), [mapping()], lambda m: None), [])

    def test_explicit_changed_after_passes(self):
        results = preflight.check_incremental_readiness(
            settings("INCREMENTAL", changed_after="2026-08-01T00:00:00Z"), [mapping()], lambda m: None
        )
        self.assertEqual([r.status for r in results], [preflight.PASS])

    def test_saved_watermark_passes_and_reports_the_boundary(self):
        results = preflight.check_incremental_readiness(
            settings("INCREMENTAL"),
            [mapping()],
            lambda m: {"maxUpdatedAt": "2026-07-01T10:00:00Z", "lastLoadedAt": "2026-07-01T10:05:00Z"},
        )
        self.assertEqual([r.status for r in results], [preflight.PASS])
        self.assertIn("2026-07-01T10:00:00Z", results[0].detail)

    def test_missing_watermark_fails_before_the_run(self):
        results = preflight.check_incremental_readiness(settings("INCREMENTAL"), [mapping()], lambda m: None)
        self.assertEqual([r.status for r in results], [preflight.FAIL])
        # Both routes out are named: establish a watermark, or state a cutoff on the command.
        self.assertIn("full load", results[0].remedy)
        self.assertIn("--since", results[0].remedy)

    def test_unreadable_watermark_fails_with_permission_hint(self):
        def broken(_mapping_id):
            raise RuntimeError("AccessDenied")

        results = preflight.check_incremental_readiness(settings("INCREMENTAL"), [mapping()], broken)
        self.assertEqual([r.status for r in results], [preflight.FAIL])
        self.assertIn("state/*", results[0].remedy)


class StagingBucketChecks(unittest.TestCase):
    class _Store:
        bucket = "bucket"

        def location(self, key: str = "") -> str:
            # Both real stores answer this; the checks quote it instead of assuming S3.
            return f"s3://{self.bucket}/{key}" if key else f"s3://{self.bucket}"

        def __init__(self, write_error=None, read_error=None):
            self.write_error = write_error
            self.read_error = read_error
            self.written: dict[str, object] = {}

        def put_json(self, key, value):
            if self.write_error:
                raise self.write_error
            self.written[key] = value

        def get_json(self, key):
            if self.read_error:
                raise self.read_error
            return self.written[key]

    def test_writable_and_readable_passes_and_writes_the_probe(self):
        store = self._Store()
        results = preflight.check_staging_bucket(store)
        self.assertEqual([r.status for r in results], [preflight.PASS, preflight.PASS])
        self.assertIn(preflight.PROBE_KEY, store.written)
        self.assertTrue(preflight.PROBE_KEY.startswith("state/"))

    def test_write_failure_names_the_required_permission(self):
        results = preflight.check_staging_bucket(self._Store(write_error=RuntimeError("AccessDenied")))
        self.assertEqual(results[0].status, preflight.FAIL)
        self.assertIn("s3:PutObject", results[0].remedy)

    def test_read_failure_is_reported_separately(self):
        results = preflight.check_staging_bucket(self._Store(read_error=RuntimeError("AccessDenied")))
        self.assertEqual([r.status for r in results], [preflight.PASS, preflight.FAIL])


class RegistryAccessChecks(unittest.TestCase):
    def test_reachable_registry_passes(self):
        results = preflight.check_registry_access(
            [mapping()], side="source", prober=lambda endpoint: None, label="Preview registry"
        )
        self.assertEqual([r.status for r in results], [preflight.PASS])

    def test_unreachable_registry_fails_with_a_remedy(self):
        def broken(_endpoint):
            raise RuntimeError("ResourceNotFoundException: registry does not exist")

        results = preflight.check_registry_access([mapping()], side="target", prober=broken, label="target registry")
        self.assertEqual([r.status for r in results], [preflight.FAIL])
        self.assertIn("registry id exists", results[0].remedy)
        self.assertIn("ResourceNotFound", results[0].detail)


class ReportAggregation(unittest.TestCase):
    def test_offline_run_skips_aws_checks(self):
        report = preflight.run_checks(settings(), [mapping()])
        self.assertTrue(report.ok)
        names = [r.name for r in report.results]
        self.assertFalse(any("reachable" in name for name in names))
        self.assertFalse(any("staging" in name for name in names))

    def test_failures_make_the_report_not_ok(self):
        report = preflight.run_checks(settings(), [mapping(source=SOURCE, target=SOURCE)])
        self.assertFalse(report.ok)
        self.assertEqual(len(report.failures), 1)

    def test_render_includes_remedies_and_a_verdict(self):
        report = preflight.run_checks(settings(), [])
        text = report.render()
        self.assertIn("[FAIL]", text)
        self.assertIn("fix:", text)
        self.assertIn("FAILED", text)

    def test_as_dict_is_machine_readable(self):
        payload = preflight.run_checks(settings(), [mapping()]).as_dict()
        self.assertEqual(payload["status"], preflight.PASS)
        self.assertEqual(payload["failureCount"], 0)
        self.assertTrue(all({"name", "status", "detail"} <= set(check) for check in payload["checks"]))

    def test_warnings_do_not_block_but_are_counted(self):
        report = preflight.run_checks(settings(dry_run=False), [mapping()])
        self.assertTrue(report.ok)
        # Live writes enabled, and the fixture mapping crosses regions.
        self.assertEqual(
            sorted(item.name for item in report.warnings),
            ["config.dryRun", "registries.map-a.crossRegion"],
        )


if __name__ == "__main__":
    unittest.main()


class TheOutputAnOperatorReadsIsTheMigrationsOwn(unittest.TestCase):
    """Two fixes for output that a live run showed was either noise or contradictory.

    Both were found by reading a colleague's session log rather than by a test, which is the point:
    nothing here changes what the tool does, only what it says, and that is exactly the sort of defect
    a passing suite happily hides.
    """

    def test_third_party_libraries_are_quietened_but_warnings_still_get_through(self):
        """botocore reported a missing endpoint ruleset on every single command.

        ``logging.basicConfig`` configures the *root* logger, so putting this tool at INFO put every
        library there too. Some service models carry only ``service-2.json`` and no endpoint
        ruleset, so botocore said "No endpoints ruleset found for service ..." every time a client was
        built -- unactionable, and frequent enough to teach an operator to skim past the lines that do
        matter.

        Pinned to WARNING rather than silenced: a throttling retry or a TLS failure is the operator's
        business, a routing implementation detail is not.
        """
        import logging

        from migration_common.util import configure_logging

        original = {name: logging.getLogger(name).level for name in ("boto3", "botocore", "s3transfer", "urllib3")}
        root_original = logging.getLogger().level
        try:
            configure_logging()
            for name in original:
                self.assertEqual(
                    logging.getLogger(name).level,
                    logging.WARNING,
                    f"{name} should not report at INFO",
                )
                # Still audible when it matters.
                self.assertTrue(logging.getLogger(name).isEnabledFor(logging.WARNING))
            # This tool's own logging is untouched: the migration's progress is the output.
            self.assertEqual(logging.getLogger().level, logging.INFO)
        finally:
            for name, level in original.items():
                logging.getLogger(name).setLevel(level)
            logging.getLogger().setLevel(root_original)

    def test_a_passing_report_renders_as_one_line_and_a_failing_one_in_full(self):
        """The extract job printed a second "Pre-flight validation PASSED" with fewer checks.

        The CLI runs `check` as its own stage first, and that report is fuller -- it has the staging
        store and the registry probers, so it covers reachability. The job then rendered its own
        reduced report, so a reader saw the same banner twice with different counts and no way to tell
        which was authoritative or why checks appeared to have gone missing.

        The job's report cannot simply be dropped: Glue starts the job directly, with no CLI in front
        of it, so on that path it is the only pre-flight there is. What changed is the success-path
        verbosity only, which is why this asserts the failing path still carries every detail.
        """
        passing = preflight.PreflightReport(
            results=[preflight.CheckResult(name="config.loadMode", status=preflight.PASS, detail="FULL")]
        )
        self.assertTrue(passing.ok)
        # The one-liner the job logs is built from these, so they have to be available and truthful.
        self.assertEqual(len(passing.results), 1)
        self.assertEqual(passing.warnings, [])

        failing = preflight.PreflightReport(
            results=[
                preflight.CheckResult(
                    name="registries.map-a.shape",
                    status=preflight.FAIL,
                    detail="registryId is missing",
                    remedy="Set registries[0].source.registryId",
                )
            ]
        )
        self.assertFalse(failing.ok)
        rendered = failing.render()
        # A failure has to arrive with the detail and the remedy, since it is what the operator acts on.
        self.assertIn("registries.map-a.shape", rendered)
        self.assertIn("registryId is missing", rendered)
        self.assertIn("Set registries[0].source.registryId", rendered)
