"""Tests for the HTML run report.

The page exists so a person can verify a migration without knowing which fields of summary.json
matter, so what is pinned here is the review it presents: which checks appear, what each one
concludes for a given run, and that nothing in a registry id or an error message can break out of
the markup.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.report_html import (
    ATTENTION,
    INFO,
    OK,
    build_checks,
    render_extract_report,
    render_report,
)


def report(**overrides):
    """A clean, successful live run of three records."""
    base = {
        "schemaVersion": 1,
        "runId": "20260806T120000Z-abcd1234",
        "attemptId": "attempt-1",
        "stage": "TRANSFORM_LOAD",
        "status": "SUCCEEDED",
        "dryRun": False,
        "startedAt": "2026-08-06T12:00:00Z",
        "completedAt": "2026-08-06T12:01:00Z",
        "processedRecordCount": 3,
        "errorCount": 0,
        "artifacts": {"s3://bucket/reports/summary.json": "The same report as data"},
        "approval": {
            "matchSourceStatus": True,
            "sourceStatusCounts": {"APPROVED": 2, "DRAFT": 1},
            "targetStatusCounts": {"APPROVED": 2, "DRAFT": 1},
            "statusesApplied": 2,
            "statusesNotApplied": 0,
            "recordsNeedingResubmission": 0,
            "note": "2 record(s) were moved to the status they hold in the Preview registry.",
        },
        "registries": [
            {
                "mappingId": "map-a",
                "source": "111122223333/us-east-1/reg-preview",
                "target": "111122223333/us-east-1/reg-new",
                "extracted": 3,
                "created": 3,
                "updated": 0,
                "existing": 0,
                "dryRun": 0,
                "failed": 0,
            }
        ],
    }
    base.update(overrides)
    return base


def check(checks, what):
    return next(entry for entry in checks if entry["what"] == what)


def extract_summary(**overrides):
    """A clean extract of the same three records."""
    base = {
        "runId": "20260806T120000Z-abcd1234",
        "readyForTransform": True,
        "totals": {"records": 3, "registries": 1, "warnings": 0, "duplicateNames": 0},
        "registries": [
            {
                "mappingId": "map-a",
                "recordCount": 3,
                "recordTypeCounts": {"MCP": 2, "CUSTOM": 1},
                "sourceStatusCounts": {"APPROVED": 2, "DRAFT": 1},
            }
        ],
    }
    base.update(overrides)
    return base


class ChecksFromTheReadSide(unittest.TestCase):
    """A load report cannot answer these, so the page has to take them from the extract report."""

    def test_a_clean_extract_is_reported_as_clean(self):
        entry = check(build_checks(report(), extract_summary()), "Preview source records were extracted successfully")
        self.assertEqual(entry["status"], OK)
        self.assertIn("3 record(s) were read from 1 configured Preview source registry", entry["detail"])

    def test_a_partial_extract_is_called_out_because_the_load_looks_complete(self):
        # A load can account for all staged records while extraction covered only part of Preview.
        entry = check(
            build_checks(report(), extract_summary(readyForTransform=False)),
            "Preview source records were extracted successfully",
        )
        self.assertEqual(entry["status"], ATTENTION)
        self.assertIn("part of the configured Preview source data", entry["detail"])
        self.assertIn("extract again", entry["todo"])

    def test_inferred_record_types_are_shown_because_preview_had_none(self):
        entry = check(
            build_checks(report(), extract_summary()),
            "Record types were inferred from each record's descriptor shape",
        )
        self.assertIn("MCP: 2", entry["detail"])
        self.assertIn("CUSTOM: 1", entry["detail"])

    def test_extraction_warnings_are_surfaced(self):
        summary = extract_summary(totals={"records": 3, "warnings": 2})
        entry = check(build_checks(report(), summary), "Nothing was flagged while reading")
        self.assertEqual(entry["status"], ATTENTION)
        self.assertIn("2 warning(s)", entry["detail"])

    def test_an_incremental_window_is_stated_so_absent_records_are_not_a_surprise(self):
        summary = extract_summary(incrementalWindow={"changedAfter": "2026-08-01T00:00:00Z"})
        entry = check(build_checks(report(), summary), "The incremental window is the one you meant")
        self.assertIn("2026-08-01T00:00:00Z", entry["detail"])

    def test_without_an_extract_summary_the_page_still_renders(self):
        # A run loaded before the page existed may have no extract summary to hand.
        whats = [entry["what"] for entry in build_checks(report(), None)]
        self.assertNotIn("Preview source records were extracted successfully", whats)
        self.assertIn("Load attempted every extracted record", whats)


class ChecksForACleanRun(unittest.TestCase):
    def test_the_reviewable_questions_are_all_present(self):
        # The page is the review, so the list of questions is part of the contract. Background
        # that never varies with the run's outcome (descriptor reshaping, the id crosswalk, the
        # namespace rename) is not a check -- it lives in the reference notes instead.
        self.assertEqual(
            [entry["what"] for entry in build_checks(report())],
            [
                "Load attempted every extracted record",
                "Failed records",
                "Verify each target contains the expected records",
                "Approval status carried across",
            ],
        )

    def test_a_clean_run_needs_no_attention(self):
        self.assertEqual([entry for entry in build_checks(report()) if entry["status"] == ATTENTION], [])

    def test_accounting_covers_every_extracted_record(self):
        entry = check(build_checks(report()), "Load attempted every extracted record")
        self.assertEqual(entry["status"], OK)
        self.assertIn("All 3 extracted record(s) reached a final outcome", entry["detail"])
        self.assertIn("3 created", entry["detail"])
        self.assertIn("No staged records were skipped", entry["detail"])


class ChecksThatNeedAttention(unittest.TestCase):
    def test_a_failed_record_is_called_out_with_what_to_do(self):
        failing = report(
            status="FAILED",
            errorCount=1,
            registries=[
                dict(
                    report()["registries"][0],
                    created=2,
                    failed=1,
                    failures="s3://bucket/failures/mapping=map-a.json",
                )
            ],
        )
        entry = check(build_checks(failing), "Failed records")
        self.assertEqual(entry["status"], ATTENTION)
        self.assertIn("1 of 3 extracted record(s) failed", entry["detail"])
        self.assertIn("failed-record section", entry["todo"])
        self.assertIn("payload and traceback", entry["todo"])

    def test_records_not_reached_by_the_load_are_called_out(self):
        # Fewer outcomes than extracted records means the load stopped part-way.
        partial = report(registries=[dict(report()["registries"][0], extracted=9, created=3)])
        entry = check(build_checks(partial), "Load attempted every extracted record")
        self.assertEqual(entry["status"], ATTENTION)
        self.assertIn("3 of 9", entry["detail"])
        self.assertIn("Re-run the load", entry["todo"])

    def test_a_status_left_behind_is_called_out_as_invisible_to_the_data_plane(self):
        stranded = report(
            approval=dict(
                report()["approval"],
                statusesNotApplied=1,
                recordsNeedingResubmission=1,
                note="1 could not be.",
            )
        )
        entry = check(build_checks(stranded), "Approval status carried across")
        self.assertEqual(entry["status"], ATTENTION)
        self.assertIn("not returned by data-plane search", entry["todo"])


class DryRunReport(unittest.TestCase):
    def test_a_dry_run_says_nothing_was_written_and_how_to_load_it(self):
        checks = build_checks(report(dryRun=True))
        entry = check(checks, "Nothing was written")
        self.assertEqual(entry["status"], INFO)
        self.assertIn("run --live --resume", entry["todo"])
        # Comparing counts against the source is meaningless before anything is written.
        self.assertNotIn("Verify each target contains the expected records", [entry["what"] for entry in checks])


class RenderedPage(unittest.TestCase):
    def test_the_page_is_self_contained(self):
        page = render_report(report())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)
        # No scripts and no external requests: this is opened off a filesystem, often in an account
        # with no internet route.
        for forbidden in ("<script", "http://", "https://fonts", "<link"):
            self.assertNotIn(forbidden, page)

    def test_the_numbers_and_the_verdicts_are_on_the_page(self):
        page = render_report(report())
        self.assertIn("20260806T120000Z-abcd1234", page)
        self.assertIn("111122223333/us-east-1/reg-preview", page)
        self.assertIn("Load attempted every extracted record", page)
        self.assertIn("LIVE", page)
        self.assertIn("s3://bucket/reports/summary.json", page)

    def test_a_dry_run_page_says_so_at_the_top(self):
        page = render_report(report(dryRun=True))
        self.assertIn("DRY RUN", page)
        self.assertIn("Dry run -- nothing was written to the target registry", page)
        # The banner has to say what a live run would do, not just that this one did nothing.
        self.assertIn("would create", page)
        self.assertIn("--live", page)

    def test_content_from_the_registry_cannot_break_out_of_the_markup(self):
        # Record names, ids and target error text all reach this page, and none of them are ours.
        hostile = report(
            registries=[
                dict(
                    report()["registries"][0],
                    mappingId='<img src=x onerror="alert(1)">',
                    failed=1,
                    failureDetails=[
                        {
                            "oldRecordId": "preview-1",
                            "name": "hostile-record",
                            "recordType": "MCP_SERVER",
                            "error": "<script>alert('reason')</script>",
                        }
                    ],
                    failures="<script>alert('failures')</script>",
                )
            ]
        )
        page = render_report(hostile)
        self.assertNotIn("<img", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;img", page)
        self.assertIn("&lt;script&gt;alert(&#x27;reason&#x27;)&lt;/script&gt;", page)

    def test_a_failed_run_shows_each_record_error_and_full_diagnostics(self):
        failing = report(
            status="FAILED",
            registries=[
                dict(
                    report()["registries"][0],
                    failed=1,
                    failureDetails=[
                        {
                            "oldRecordId": "preview-record-1",
                            "name": "orders-agent",
                            "recordType": "AGENT",
                            "error": "A record named orders-agent already exists in the target registry",
                        }
                    ],
                    failures="s3://bucket/f.json",
                )
            ],
        )
        page = render_report(failing)
        self.assertIn("Failed records and error reasons", page)
        self.assertIn("orders-agent", page)
        self.assertIn("preview-record-1", page)
        self.assertIn("A record named orders-agent already exists in the target registry", page)
        self.assertIn("s3://bucket/f.json", page)

    def test_a_run_with_no_approval_block_still_renders(self):
        page = render_report(report(approval=None))
        self.assertIn("Migration review and required actions", page)
        self.assertNotIn("Approval status carried across", page)

    def test_a_clean_run_shows_the_clear_banner_not_the_attention_one(self):
        # The headline states the outcome in records, not a count of internal checks: the first
        # question a reviewer has is what is in the target registry now.
        page = render_report(report())
        self.assertIn("are now in your target registries", page)
        self.assertIn("Every check passed", page)
        self.assertNotIn("need your attention", page)

    def test_a_run_needing_attention_shows_the_attention_banner(self):
        failing = report(status="FAILED", errorCount=1, registries=[dict(report()["registries"][0], failed=1)])
        page = render_report(failing)
        self.assertIn("need your attention", page)

    def test_background_that_never_varies_is_in_reference_notes_not_the_checklist(self):
        # These are true of every migration, not a verdict on this one -- moved out of the
        # scored checklist so the checklist only shows things that actually vary run to run.
        page = render_report(report())
        self.assertIn("Background that applies to every migration", page)
        self.assertIn("Descriptors changed shape", page)
        self.assertIn("Every record has a new recordId", page)
        self.assertIn("The service namespace changes", page)

    def test_a_failing_run_names_how_many_checks_need_attention_in_the_headline(self):
        failing = report(
            status="FAILED",
            errorCount=1,
            registries=[dict(report()["registries"][0], created=2, failed=1)],
        )
        page = render_report(failing)
        self.assertIn("1 check needs your attention", page)

    def test_the_headline_pluralises_correctly_for_more_than_one_check(self):
        # "1 check needs" vs "2 checks need" -- both the noun and the verb have to agree.
        stranded = report(
            status="FAILED",
            errorCount=1,
            registries=[dict(report()["registries"][0], created=2, failed=1)],
            approval=dict(report()["approval"], statusesNotApplied=1, recordsNeedingResubmission=1),
        )
        page = render_report(stranded)
        self.assertIn("2 checks need your attention", page)
        self.assertNotIn("checks needs", page)
        self.assertNotIn("check need ", page)

    def test_the_proportional_bar_reflects_the_outcome_split(self):
        mixed = report(registries=[dict(report()["registries"][0], created=1, updated=1, existing=1, failed=1)])
        page = render_report(mixed)
        self.assertIn('class="bar"', page)
        self.assertIn("seg-created", page)
        self.assertIn("seg-updated", page)
        self.assertIn("seg-unchanged", page)
        self.assertIn("seg-failed", page)

    def test_artifacts_are_collapsed_by_default_but_present_in_the_markup(self):
        page = render_report(report())
        self.assertIn('<details class="artifacts">', page)
        self.assertIn("Where everything is", page)
        self.assertIn("s3://bucket/reports/summary.json", page)

    def test_a_stale_replay_fingerprint_field_is_not_rendered(self):
        # replayConfiguration never actually carries a "fingerprint" key in a real report (see
        # _validate_replay_configuration) -- this pinned a row that could never render for real.
        page = render_report(report(replayConfiguration={"fingerprint": "should-never-show"}))
        self.assertNotIn("should-never-show", page)
        self.assertNotIn("Configuration fingerprint", page)


class ExtractReport(unittest.TestCase):
    def test_the_page_is_self_contained(self):
        page = render_extract_report(extract_summary())
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("</html>", page)
        for forbidden in ("<script", "http://", "https://fonts", "<link"):
            self.assertNotIn(forbidden, page)

    def test_a_clean_extract_says_review_then_decide(self):
        page = render_extract_report(extract_summary(status="SUCCEEDED"))
        self.assertIn("SUCCEEDED", page)
        self.assertIn("Record types were inferred", page)
        self.assertIn("Ready to load", page)

    def test_a_failed_extract_says_do_not_load(self):
        page = render_extract_report(extract_summary(readyForTransform=False, status="FAILED"))
        self.assertIn("Not ready to load", page)
        # It must also say why that matters, not just refuse.
        self.assertIn("only part of your data", page)

    def test_content_from_the_registry_cannot_break_out_of_the_markup(self):
        hostile = extract_summary(
            registries=[dict(extract_summary()["registries"][0], mappingId='<img src=x onerror="alert(1)">')]
        )
        page = render_extract_report(hostile)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;img", page)

    def test_the_registry_table_and_artifacts_are_on_the_page(self):
        summary = dict(
            extract_summary(),
            artifacts={"s3://bucket/reports/extract-summary.json": "The same report as data"},
        )
        page = render_extract_report(summary)
        self.assertIn("map-a", page)
        self.assertIn('<details class="artifacts">', page)
        self.assertIn("s3://bucket/reports/extract-summary.json", page)

    def test_a_not_ready_extract_leads_with_do_not_load(self):
        page = render_extract_report(extract_summary(readyForTransform=False, status="FAILED"))
        self.assertIn("Not ready to load", page)
        self.assertNotIn("record(s) staged", page)

    def test_warnings_are_visually_flagged_on_the_stat_tile(self):
        page = render_extract_report(extract_summary(totals={"records": 3, "warnings": 2}))
        self.assertIn('<div class="stat warn">', page)


if __name__ == "__main__":
    unittest.main()
