"""Tests for the single Python entrypoint the CLI drives.

Two things matter here. The dispatcher must route to the right stage and pass arguments through
untouched, so the CLI and Glue reach identical code. And ``--live`` must be the only thing that can
turn a review run into a live one -- it decides whether records reach a customer's target registry, so
it is pinned from both ends: the dispatcher forwards it, and the settings layer applies it.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common import __main__ as engine
from migration_common.local_store import LocalStore
from migration_common.settings import (
    ConfigurationError,
    apply_run_overrides,
    live_override,
    parse_job_arguments,
)


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.calls: list[tuple[str, list[str]]] = []

        def record(name):
            def fake(argv):
                self.calls.append((name, list(argv)))

            return fake

        for module, name in ((engine.extract_job, "extract"), (engine.transform_load_job, "load")):
            original = module.main
            module.main = record(name)
            self.addCleanup(setattr, module, "main", original)

    def test_extract_and_load_receive_every_argument_unchanged(self):
        # Paths only have to be forwarded verbatim, never opened, but they still go through
        # gettempdir() rather than a literal /tmp so no scanner has to guess that.
        staging = os.path.join(tempfile.gettempdir(), "s")
        config = os.path.join(tempfile.gettempdir(), "c.json")
        argv = ["--config-file", config, "--local-dir", staging, "--run-id", "r-1"]
        self.assertEqual(engine.main(["extract", *argv]), 0)
        self.assertEqual(engine.main(["load", *argv, "--live", "true"]), 0)
        self.assertEqual(
            self.calls,
            [("extract", argv), ("load", [*argv, "--live", "true"])],
        )

    def test_an_unknown_command_does_no_work(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(engine.main(["migrate-everything"]), 2)
        self.assertEqual(self.calls, [])

    def test_help_does_no_work(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(engine.main(["--help"]), 0)
        self.assertEqual(self.calls, [])

    def test_every_advertised_command_has_a_handler(self):
        """Each name in COMMANDS must resolve to a handler.

        This used to assert that each name appeared as a *substring of the module docstring*, which
        cannot catch what it was named for: substring matching passes spuriously ("load" matches
        "payload", "report" matches "reports"), and a docstring mention is not a handler. The
        dispatcher it was guarding fell through to the load stage, so a command added to COMMANDS
        and forgotten in the chain silently wrote to a target registry.
        """
        for command in engine.COMMANDS:
            self.assertIn(command, engine._HANDLERS, command)
            self.assertTrue(callable(engine._HANDLERS[command]), command)

    def test_no_handler_is_unreachable(self):
        """The other direction: a handler nobody can invoke is dead code, or a missing COMMANDS entry."""
        self.assertEqual(sorted(engine._HANDLERS), sorted(engine.COMMANDS))

    def test_a_command_with_no_handler_is_refused_not_guessed(self):
        """An advertised-but-unhandled command must raise, never fall through to another stage."""
        original = engine.COMMANDS
        engine.COMMANDS = (*original, "brand-new-command")
        try:
            with self.assertRaises(ConfigurationError) as raised:
                engine.main(["brand-new-command"])
        finally:
            engine.COMMANDS = original
        self.assertIn("no handler", str(raised.exception))
        # Crucially: it did not run a stage.
        self.assertEqual(self.calls, [])


class LiveIsTheOnlyWayToWrite(unittest.TestCase):
    def _settings(self, dry_run: bool) -> dict:
        return {"load": {"dryRun": dry_run}}

    def test_absent_live_leaves_the_configured_value_alone(self):
        self.assertIsNone(live_override(parse_job_arguments([])))
        settings = apply_run_overrides(self._settings(True), parse_job_arguments([]))
        self.assertTrue(settings["load"]["dryRun"])

    def test_live_true_enables_writes(self):
        settings = apply_run_overrides(self._settings(True), parse_job_arguments(["--live", "true"]))
        self.assertFalse(settings["load"]["dryRun"])

    def test_live_false_forces_a_dry_run_even_when_configured_live(self):
        """The CLI always states its intent, so a stored dryRun=false cannot surprise anyone."""
        settings = apply_run_overrides(self._settings(False), parse_job_arguments(["--live", "false"]))
        self.assertTrue(settings["load"]["dryRun"])

    def test_a_bare_flag_means_live(self):
        self.assertIs(live_override(parse_job_arguments(["--live"])), True)

    def test_glue_style_naming_works_too(self):
        self.assertIs(live_override(parse_job_arguments(["--LIVE", "true"])), True)

    def test_an_unreadable_value_is_refused_rather_than_guessed(self):
        with self.assertRaises(ConfigurationError):
            live_override(parse_job_arguments(["--live", "maybe"]))

    def test_the_environment_can_never_enable_live_writes(self):
        os.environ["LIVE"] = "true"
        self.addCleanup(os.environ.pop, "LIVE", None)
        self.assertIsNone(live_override(parse_job_arguments([])))


class ScopeIsDecidedPerRun(unittest.TestCase):
    """`run --incremental` / `--since` override the mode without editing the configuration.

    A cutover catch-up is the one operation performed under time pressure, so it has to be a flag
    rather than a file edit. These pin that the override lands on the same settings the extract
    stage reads to resolve its cutoff.
    """

    def _load(self, argv: list[str], *, mode: str = "FULL", changed_after=None) -> dict:
        settings = {"load": {"mode": mode, "changedAfter": changed_after, "dryRun": True}}
        return apply_run_overrides(settings, parse_job_arguments(argv))["load"]

    def test_absent_override_leaves_the_configured_mode_alone(self):
        self.assertEqual(self._load([], mode="INCREMENTAL")["mode"], "INCREMENTAL")

    def test_incremental_can_be_asked_for_on_the_command_line(self):
        self.assertEqual(self._load(["--load-mode", "INCREMENTAL"])["mode"], "INCREMENTAL")

    def test_full_can_be_asked_for_over_a_configured_incremental(self):
        self.assertEqual(self._load(["--load-mode", "FULL"], mode="INCREMENTAL")["mode"], "FULL")

    def test_the_mode_is_case_insensitive_but_still_closed(self):
        self.assertEqual(self._load(["--load-mode", "incremental"])["mode"], "INCREMENTAL")
        with self.assertRaises(ConfigurationError):
            self._load(["--load-mode", "SINCE_TUESDAY"])

    def test_an_explicit_cutoff_is_carried_through(self):
        load = self._load(["--load-mode", "INCREMENTAL", "--changed-after", "2026-08-01T00:00:00Z"])
        self.assertEqual(load["changedAfter"], "2026-08-01T00:00:00Z")

    def test_an_empty_cutoff_falls_back_to_the_watermark(self):
        load = self._load(["--changed-after", ""], mode="INCREMENTAL", changed_after="2026-01-01T00:00:00Z")
        self.assertIsNone(load["changedAfter"])

    def test_glue_style_naming_works_for_the_scope_too(self):
        self.assertEqual(self._load(["--LOAD_MODE", "INCREMENTAL"])["mode"], "INCREMENTAL")

    def test_the_environment_cannot_change_the_scope(self):
        os.environ["LOAD_MODE"] = "INCREMENTAL"
        self.addCleanup(os.environ.pop, "LOAD_MODE", None)
        self.assertEqual(self._load([])["mode"], "FULL")


class OverridesAreValidatedLikeTheFileIs(unittest.TestCase):
    """An override arrives after the document was validated, so it is re-checked.

    Without this, `--changed-after nonsense` would reach the extract stage and fail there, deep in a
    run, instead of before anything is read.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "migration.config.json"
        self.path.write_text(
            json.dumps(
                {
                    "engine": {"account": "111122223333", "region": "us-west-2"},
                    "registries": [
                        {
                            "id": "map-a",
                            "source": {
                                "accountId": "111122223333",
                                "region": "us-east-1",
                                "registryId": "src",
                            },
                            "target": {
                                "accountId": "111122223333",
                                "region": "us-west-2",
                                "registryId": "tgt",
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def _resolve(self, argv: list[str]):
        from migration_common.settings import resolve_configuration

        return resolve_configuration(parse_job_arguments(["--config-file", str(self.path), *argv]))

    def test_a_valid_override_reaches_the_settings(self):
        settings, _mappings, _source = self._resolve(
            ["--load-mode", "INCREMENTAL", "--changed-after", "2026-08-01T00:00:00Z"]
        )
        self.assertEqual(settings["load"]["mode"], "INCREMENTAL")
        self.assertEqual(settings["load"]["changedAfter"], "2026-08-01T00:00:00Z")

    def test_an_unparseable_cutoff_is_refused_before_anything_is_read(self):
        with self.assertRaises(ConfigurationError) as ctx:
            self._resolve(["--load-mode", "INCREMENTAL", "--changed-after", "last Tuesday"])
        self.assertIn("ISO-8601", str(ctx.exception))


class ReportsAreFoundWithoutARunId(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = LocalStore(Path(self.temp.name))

    def _write(
        self,
        run_id: str,
        *,
        attempt: str | None = None,
        status: str = "SUCCEEDED",
        started_at: str | None = None,
        approval: dict | None = None,
    ) -> None:
        extract: dict = {
            "status": "SUCCEEDED",
            "readyForTransform": True,
            "totals": {"records": 2, "registries": 1},
        }
        if started_at:
            extract["startedAt"] = started_at
        self.store.put_json(f"reports/run_id={run_id}/extract-summary.json", extract)
        if attempt:
            summary: dict = {
                "status": status,
                "dryRun": False,
                "startedAt": "2026-07-28T00:00:00Z",
                "registries": [{"mappingId": "map-a", "created": 2}],
                "artifacts": {},
            }
            if approval is not None:
                summary["approval"] = approval
            self.store.put_json(f"reports/run_id={run_id}/attempt={attempt}/summary.json", summary)

    def test_the_newest_run_is_used_when_none_is_named(self):
        self._write("20260101T000000Z-aaaaaaaa")
        self._write("20260202T000000Z-bbbbbbbb")
        self.assertEqual(engine._latest_run_id(self.store), "20260202T000000Z-bbbbbbbb")

    def test_no_runs_at_all_is_not_a_crash(self):
        self.assertIsNone(engine._latest_run_id(self.store))

    def test_a_caller_supplied_run_id_does_not_reorder_the_runs(self):
        """`--run-id` takes any string, so the id cannot be trusted to sort chronologically.

        The real case: a hand-named run from an earlier version of the tool sits in the same bucket
        as generated ones. 'w' sorts after '2', so a plain string sort called the oldest run in the
        bucket the newest and loading without --run-id picked it.
        """
        self._write("20260730T115003Z-2167cf8f", started_at="2026-07-30T11:50:08Z")
        self._write("wr-shapes-1785128874", started_at="2026-07-27T05:08:13Z")

        self.assertEqual(engine._latest_run_id(self.store), "20260730T115003Z-2167cf8f")
        self.assertEqual(
            engine._run_ids(self.store),
            ["wr-shapes-1785128874", "20260730T115003Z-2167cf8f"],
            "runs are ordered by when they ran, oldest first",
        )

    def test_a_run_with_no_recorded_start_sorts_oldest(self):
        """Being unable to date a run is a reason not to call it newest, not to hide it."""
        self._write("zzz-undated")
        self._write("20260730T115003Z-2167cf8f", started_at="2026-07-30T11:50:08Z")

        self.assertEqual(engine._latest_run_id(self.store), "20260730T115003Z-2167cf8f")
        self.assertIn("zzz-undated", engine._run_ids(self.store))

    def test_an_unparseable_start_is_treated_as_undated_rather_than_crashing(self):
        self._write("bad-timestamp", started_at="not-a-timestamp")
        self._write("20260730T115003Z-2167cf8f", started_at="2026-07-30T11:50:08Z")

        self.assertEqual(engine._latest_run_id(self.store), "20260730T115003Z-2167cf8f")

    def test_a_status_that_could_not_be_applied_is_named_in_the_report(self):
        """A record that loaded but kept the wrong status is the one failure a clean run can hide.

        It is not a record failure -- the content is in the target registry -- so it never reaches errorCount, and
        before this it appeared only in summary.json. A record left below its source status is
        invisible to data-plane search, which is the whole point of migrating it.
        """
        self._write(
            "run-1",
            attempt="a1",
            approval={"statusesNotApplied": 2, "recordsNeedingResubmission": 0},
        )
        rendered = engine._render_report(
            "run-1",
            self.store,
            self.store.get_json("reports/run_id=run-1/extract-summary.json"),
            engine._attempt_summaries(self.store, "run-1"),
        )
        self.assertIn("2 record(s) loaded but kept the wrong status", rendered)

    def test_a_run_where_every_status_applied_stays_quiet(self):
        self._write(
            "run-1",
            attempt="a1",
            approval={"statusesNotApplied": 0, "recordsNeedingResubmission": 0},
        )
        rendered = engine._render_report(
            "run-1",
            self.store,
            self.store.get_json("reports/run_id=run-1/extract-summary.json"),
            engine._attempt_summaries(self.store, "run-1"),
        )
        self.assertNotIn("wrong status", rendered)

    def test_attempts_are_returned_oldest_first(self):
        self._write("run-1", attempt="a1")
        summaries = engine._attempt_summaries(self.store, "run-1")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["registries"][0]["created"], 2)

    def test_a_rendered_report_states_whether_anything_was_written(self):
        self._write("run-1", attempt="a1")
        rendered = engine._render_report(
            "run-1",
            self.store,
            self.store.get_json("reports/run_id=run-1/extract-summary.json"),
            engine._attempt_summaries(self.store, "run-1"),
        )
        self.assertIn("run-1", rendered)
        self.assertIn("LIVE", rendered)
        self.assertIn("created=2", rendered)

    def test_a_run_with_no_load_yet_says_so_rather_than_looking_empty(self):
        self._write("run-1")
        rendered = engine._render_report(
            "run-1",
            self.store,
            self.store.get_json("reports/run_id=run-1/extract-summary.json"),
            [],
        )
        self.assertIn("not run yet", rendered)


class TheTargetModelPrerequisiteIsPrintedOnce(unittest.TestCase):
    """The note belongs to whoever is talking to the person, and only one of them is.

    `target-config` emits a create-registry command an older AWS CLI cannot run, so the note saying
    what to do about that has to travel with it. But `init` shells to `target-config --json` and
    inherits its stderr while printing its own copy in position -- which put the same six lines on
    screen twice. `--json` means something is reading this rather than someone, so the note is the
    caller's job there.
    """

    def _stderr_for(self, arguments: dict) -> str:
        import contextlib

        from migration_common import target_registry

        rendered = [{"mappingId": "m1", "command": "aws agent-registry-control create-registry ..."}]
        buffer = io.StringIO()
        # Reproduces the one decision under test: print the note, or leave it to the caller.
        with contextlib.redirect_stderr(buffer):
            if not engine.flag(arguments, "JSON") and any(entry.get("command") for entry in rendered):
                print("\n" + target_registry.create_registry_prerequisite(), file=sys.stderr)
        return buffer.getvalue()

    def test_a_person_running_it_directly_gets_the_note(self):
        self.assertIn("Invalid choice", self._stderr_for({}))

    def test_a_json_consumer_does_not(self):
        self.assertEqual(self._stderr_for({"JSON": "true"}), "")


class ClearingAStrandedChangesetShell(unittest.TestCase):
    """`deploy` has to be able to recover from the stack shell a failed deploy leaves behind.

    Creating a changeset for a stack that does not exist yet creates it in REVIEW_IN_PROGRESS,
    holding no resources. Because the stack carries EnableTerminationProtection from that first
    changeset, it cannot delete itself -- so a deploy that could not get confirmation (no TTY to
    prompt at) leaves a stack that blocks every later deploy with "cannot be deleted while
    TerminationProtection is enabled". This is the recovery path; it deletes, so what it will and
    will not act on is pinned here.
    """

    def _run(self, stack: dict | None, resources: list | None = None):
        calls: list[str] = []

        class FakeCloudFormation:
            def describe_stacks(self, StackName: str):
                calls.append(f"describe:{StackName}")
                if stack is None:
                    from botocore.exceptions import ClientError

                    raise ClientError(
                        {
                            "Error": {
                                "Code": "ValidationError",
                                "Message": f"Stack with id {StackName} does not exist",
                            }
                        },
                        "DescribeStacks",
                    )
                return {"Stacks": [stack]}

            def list_stack_resources(self, StackName: str):
                calls.append("list_resources")
                return {"StackResourceSummaries": resources or []}

            def update_termination_protection(self, **kwargs):
                calls.append(f"protection:{kwargs['EnableTerminationProtection']}")
                return {}

            def delete_stack(self, StackName: str):
                calls.append(f"delete:{StackName}")
                return {}

            def get_waiter(self, name: str):
                calls.append(f"waiter:{name}")

                class Waiter:
                    def wait(self, **_kwargs):
                        calls.append("waited")

                return Waiter()

        class FakeSession:
            def __init__(self, **_kwargs) -> None:
                pass

            def client(self, name: str):
                assert name == "cloudformation", f"must not talk to {name}"
                return FakeCloudFormation()

        import boto3

        original = boto3.session.Session
        boto3.session.Session = FakeSession  # type: ignore[assignment]
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = engine.clear_pending_stack({"STACK_NAME": "AStack", "REGION": "us-west-2"})
            return code, json.loads(buffer.getvalue().strip() or "{}"), calls
        finally:
            boto3.session.Session = original  # type: ignore[assignment]

    def test_an_empty_review_stack_has_its_protection_dropped_then_is_deleted(self):
        code, result, calls = self._run({"StackStatus": "REVIEW_IN_PROGRESS", "EnableTerminationProtection": True})
        self.assertEqual(code, 0)
        self.assertTrue(result["cleared"])
        # Protection off before the delete, or the delete is refused -- order is the whole point.
        self.assertLess(calls.index("protection:False"), calls.index("delete:AStack"))
        self.assertIn("waited", calls)

    def test_a_review_stack_without_protection_is_just_deleted(self):
        _code, result, calls = self._run({"StackStatus": "REVIEW_IN_PROGRESS", "EnableTerminationProtection": False})
        self.assertTrue(result["cleared"])
        self.assertNotIn("protection:False", calls)
        self.assertIn("delete:AStack", calls)

    def test_a_real_stack_is_left_alone(self):
        """Anything other than REVIEW_IN_PROGRESS is a deployment, not a leftover shell."""
        for status in ("CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"):
            with self.subTest(status=status):
                code, result, calls = self._run({"StackStatus": status, "EnableTerminationProtection": True})
                self.assertEqual(code, 0)
                self.assertFalse(result["cleared"])
                self.assertIn(status, result["reason"])
                self.assertNotIn("delete:AStack", calls)
                self.assertNotIn("protection:False", calls)

    def test_a_review_stack_that_somehow_holds_resources_is_refused(self):
        """The emptiness is verified, not assumed: this function deletes."""
        code, result, calls = self._run(
            {"StackStatus": "REVIEW_IN_PROGRESS", "EnableTerminationProtection": True},
            resources=[{"LogicalResourceId": "Unexpected"}],
        )
        self.assertEqual(code, 0)
        self.assertFalse(result["cleared"])
        self.assertIn("resource(s) present", result["reason"])
        self.assertNotIn("delete:AStack", calls)

    def test_no_stack_at_all_is_not_an_error(self):
        """A first deploy has nothing to clear, and must not be blocked by saying so."""
        code, result, calls = self._run(None)
        self.assertEqual(code, 0)
        self.assertFalse(result["cleared"])
        self.assertNotIn("delete:AStack", calls)


class ListingKeysMatchesTheS3Store(unittest.TestCase):
    """`report` finds a run by listing, so the two stores must answer the same question."""

    def test_local_listing_is_prefix_filtered_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory))
            store.put_json("reports/run_id=b/summary.json", {})
            store.put_json("reports/run_id=a/summary.json", {})
            store.put_json("runs/run_id=a/raw.json", {})
            self.assertEqual(
                store.list_keys("reports/"),
                ["reports/run_id=a/summary.json", "reports/run_id=b/summary.json"],
            )


if __name__ == "__main__":
    unittest.main()
