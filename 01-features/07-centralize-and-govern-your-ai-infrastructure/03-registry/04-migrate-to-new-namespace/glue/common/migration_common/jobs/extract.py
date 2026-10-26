"""Extract job logic: read Preview registry records into replayable S3 JSONL staging.

For each configured mapping this assumes the source role (when set), pages through the
Preview API, and writes records to ``runs/run_id=<id>/raw/mapping=<id>/`` (partitioned by
mapping, mirroring the transformed layout). It emits a per-mapping manifest, a run-level
extract manifest (with the replay fingerprint and per-object integrity metadata), and a
human-readable extract summary report. Extraction never writes to any target registry.

Invoked by the ``glue/extract.py`` shim via :func:`run`.
"""

from __future__ import annotations

import logging
import sys
import traceback
from typing import Any

import boto3

from migration_common import preflight, report_html
from migration_common import watermark as watermark_state
from migration_common.aws_auth import invoker_for_endpoint
from migration_common.registry_api import PreviewRegistryClient
from migration_common.settings import (
    parse_job_arguments,
    replay_configuration_fingerprint,
    resolve_configuration,
    resolve_run_id,
)
from migration_common.storage import JsonArrayWriter, S3Store
from migration_common.stores import resolve_store
from migration_common.util import (
    configure_logging,
    get_path,
    public_endpoint,
    safe_segment,
    utc_now,
)

LOGGER = logging.getLogger("agent-registry-migration.extract")

# How often to report progress while reading a registry. Small enough that a slow registry still
# produces a line early, large enough that a fast one does not turn the log into a record dump.
PROGRESS_EVERY_RECORDS = 100
configure_logging()

USAGE = """\
Stage 1: extract AWS Agent Registry records from the Preview API into replayable staging.

Use the CLI rather than this stage directly:

  agent-registry-migration run           # validate, extract, then transform and report
  agent-registry-migration run --live    # the same, creating the target records

The CLI translates your configuration into the arguments below, which are also what Glue passes
(Glue uses the --UPPER_SNAKE form; both styles work):

  --config-file / --config-prefix   where the configuration lives (a file, or an SSM prefix)
  --staging-bucket / --local-dir    where this run is staged
  --run-id                          run id to write under (generated when omitted)

Requires AWS credentials with read access to the Preview registries and to the staging location.
"""


def main(argv: list[str] | None = None) -> None:
    """Run the extract stage for every configured mapping and write manifests + a summary.

    ``argv`` defaults to the process arguments; passing it lets the one-command orchestrator
    invoke this stage in-process.
    """
    arguments = parse_job_arguments(argv)
    if "help" in arguments or "h" in arguments:
        print(USAGE)
        return
    run_id = resolve_run_id(arguments, allow_generate=True)
    # Configuration first: it carries the staging bucket the deployment created, so a run needs
    # only to be told where its configuration lives.
    settings, mappings, config_source = resolve_configuration(arguments)
    store, _staging_location = resolve_store(arguments, settings, boto3_module=boto3)
    if not mappings:
        raise RuntimeError(f"No registry mappings are configured in {config_source}")

    # Fail fast on anything checkable up front (malformed ids, self-migration, duplicate mappings,
    # an INCREMENTAL run with no cutoff). These are the same checks `agent-registry-migration
    # check` runs, so what an operator validates is exactly what the job enforces. Registry reachability is left to
    # the extraction itself, which surfaces the same error per mapping.
    preflight_report = preflight.run_checks(
        settings,
        mappings,
        watermark_reader=lambda mapping_id: watermark_state.read(store, mapping_id),
    )
    # One line when it passes, the whole report when it does not.
    #
    # The CLI runs `check` as its own stage before starting this job, and that prints a fuller report
    # -- it has the staging store and the registry probers, so it covers reachability too. Rendering
    # this one as well printed "Pre-flight validation PASSED" twice in a row with different check
    # counts, and a reader cannot tell which of the two is authoritative or why checks appear to have
    # gone missing. It cannot simply be deleted, because Glue starts this job directly with no CLI in
    # front of it, so on that path this is the only pre-flight there is.
    if preflight_report.ok:
        LOGGER.info(
            "Pre-flight validation passed (%d checks)%s",
            len(preflight_report.results),
            f", {len(preflight_report.warnings)} warning(s)" if preflight_report.warnings else "",
        )
        for warning in preflight_report.warnings:
            # Carry the remedy, not just the symptom. On the Glue path this log is the only place a
            # warning appears -- there is no CLI printing the full report alongside it.
            LOGGER.warning(
                "%s: %s%s",
                warning.name,
                warning.detail,
                f" -- {warning.remedy}" if warning.remedy else "",
            )
    else:
        LOGGER.error("Pre-flight validation:\n%s", preflight_report.render())
        raise RuntimeError(
            "Pre-flight validation failed; no records were read. "
            + " | ".join(f"{item.name}: {item.detail}" for item in preflight_report.failures)
        )

    load = settings["load"]
    api_config = settings["api"]["preview"]
    record_type_path = api_config.get("response", {}).get("recordTypePath", "descriptorType")
    updated_at_path = api_config.get("response", {}).get("updatedAtPath", "updatedAt")
    records_per_object = int(load.get("recordsPerObject", 500))
    dump_extracted_records = bool(load.get("dumpExtractedRecords", True))
    run_prefix = f"runs/run_id={run_id}"
    started_at = utc_now()
    # Run lock lives with the engine's internal state so the run and report folders hold only
    # artifacts a person would want to open.
    store.put_json_if_absent(
        f"state/locks/run_id={run_id}/extract.json",
        {"runId": run_id, "stage": "EXTRACT", "createdAt": started_at},
    )
    run_manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "runId": run_id,
        "stage": "EXTRACT",
        "status": "RUNNING",
        "startedAt": started_at,
        "load": {
            "mode": load["mode"],
            "changedAfter": load.get("changedAfter"),
        },
        "replayConfiguration": {
            "schemaVersion": 1,
            "sha256": replay_configuration_fingerprint(settings),
            "scope": ["transform", "api.target"],
        },
        "registries": [],
    }
    failures: list[str] = []

    LOGGER.info("Starting extract run %s for %d registry mappings", run_id, len(mappings))
    for mapping in mappings:
        mapping_id = str(mapping["id"])
        source = mapping["source"]
        target = mapping["target"]
        # Partition raw output by mapping only. The mapping id already implies its source
        # account/region/registry (those stay in the manifest's `source` block), and keying
        # both raw and transformed output on `mapping=` makes the two mirror each other so a
        # reviewer can compare them at the same depth.
        mapping_prefix = f"{run_prefix}/raw/mapping={safe_segment(mapping_id)}"
        mapping_manifest: dict[str, Any] = {
            "mappingId": mapping_id,
            "source": public_endpoint(source),
            "target": public_endpoint(target),
            "status": "RUNNING",
            "recordCount": 0,
            "objectCount": 0,
            "objects": [],
            "recordTypeCounts": {},
            # Approval state as it stands in the Preview registry. The new version creates every record in DRAFT,
            # so this is what a reviewer needs in order to know what will still need submitting.
            "sourceStatusCounts": {},
            "warnings": [],
            "rawPrefix": mapping_prefix,
        }
        buffer: list[dict[str, Any]] = []
        part_number = 0
        # Bound before the try so the warning-collection below can reference it even when the client
        # could not be constructed.
        client: PreviewRegistryClient | None = None
        # Source updatedAt values seen for this mapping; the newest becomes the watermark that
        # transform/load commits once these records are actually in the target registry.
        observed_updated_at: list[Any] = []
        # Human-readable dump of every extracted Preview record, written into the report so it can
        # be diffed against the post-load target dump that transform/load produces. It duplicates the
        # JSONL staged under runs/, so `dumpExtractedRecords = false` turns it off for estates
        # where a second copy is not worth the storage.
        preview_dump = (
            JsonArrayWriter(
                store,
                f"reports/run_id={run_id}/extracted-records/mapping={safe_segment(mapping_id)}",
                basename="part",
                chunk_size=records_per_object,
            )
            if dump_extracted_records
            else None
        )

        try:
            # Per-mapping cutoff: an explicit changedAfter wins, otherwise the watermark saved by
            # this mapping's last successful load. Recorded in the manifest so a reviewer can see
            # exactly which window this run covered.
            previous_watermark = watermark_state.read(store, mapping_id)
            cutoff, cutoff_reason = watermark_state.resolve_cutoff(
                mapping_id=mapping_id,
                load_mode=str(load["mode"]),
                changed_after=load.get("changedAfter"),
                watermark=previous_watermark,
            )
            mapping_manifest["changedAfter"] = cutoff
            mapping_manifest["cutoffReason"] = cutoff_reason
            mapping_manifest["previousWatermark"] = previous_watermark
            LOGGER.info("Mapping %s: %s", mapping_id, cutoff_reason)

            invoker = invoker_for_endpoint(source, run_id, "extract")
            client = PreviewRegistryClient(invoker, api_config, str(source["region"]))
            for extracted in client.iter_records(
                registry_id=str(source["registryId"]),
                load_mode=str(load["mode"]),
                changed_after=cutoff,
            ):
                updated_at = get_path(extracted.record, updated_at_path) if updated_at_path else None
                if updated_at not in (None, ""):
                    observed_updated_at.append(updated_at)
                if preview_dump is not None:
                    preview_dump.append(
                        {
                            "oldRecordId": extracted.old_record_id,
                            "updatedAt": updated_at,
                            "previewRecord": extracted.record,
                        }
                    )
                buffer.append(
                    {
                        "schemaVersion": 1,
                        "runId": run_id,
                        "mappingId": mapping_id,
                        "source": source,
                        "target": target,
                        "oldRecordId": extracted.old_record_id,
                        "extractedAt": utc_now(),
                        "record": extracted.record,
                    }
                )
                mapping_manifest["recordCount"] += 1
                if mapping_manifest["recordCount"] % PROGRESS_EVERY_RECORDS == 0:
                    # Extraction reads one record at a time, so a large registry is minutes of
                    # silence otherwise -- and a silent terminal is indistinguishable from a hung
                    # one, which is when people kill a run that was working.
                    LOGGER.info(
                        "Mapping %s: %d records extracted so far",
                        mapping_id,
                        mapping_manifest["recordCount"],
                    )
                source_status = extracted.record.get("status") or "UNKNOWN"
                mapping_manifest["sourceStatusCounts"][str(source_status)] = (
                    mapping_manifest["sourceStatusCounts"].get(str(source_status), 0) + 1
                )
                record_type = get_path(extracted.record, record_type_path)
                type_key = str(record_type) if record_type not in (None, "") else "UNKNOWN"
                mapping_manifest["recordTypeCounts"][type_key] = (
                    mapping_manifest["recordTypeCounts"].get(type_key, 0) + 1
                )
                if len(buffer) >= records_per_object:
                    object_metadata = _flush(store, mapping_prefix, part_number, buffer)
                    mapping_manifest["objects"].append(object_metadata)
                    part_number += 1
                    mapping_manifest["objectCount"] += 1
                    buffer = []
            if buffer:
                object_metadata = _flush(store, mapping_prefix, part_number, buffer)
                mapping_manifest["objects"].append(object_metadata)
                mapping_manifest["objectCount"] += 1
            mapping_manifest["status"] = "SUCCEEDED"
            # Proposed, not committed: transform/load promotes this to the saved watermark only
            # after the records land in the target registry.
            mapping_manifest["candidateWatermark"] = watermark_state.build_candidate(
                mapping_id=mapping_id,
                run_id=run_id,
                extracted_at=utc_now(),
                max_updated_at=watermark_state.newest_timestamp(observed_updated_at),
                record_count=int(mapping_manifest["recordCount"]),
                previous=previous_watermark,
            )
            LOGGER.info(
                "Extracted %d records for mapping %s into %d objects",
                mapping_manifest["recordCount"],
                mapping_id,
                mapping_manifest["objectCount"],
            )
        except Exception as error:
            mapping_manifest["status"] = "FAILED"
            mapping_manifest["error"] = str(error)
            mapping_manifest["traceback"] = traceback.format_exc()
            failures.append(f"{mapping_id}: {error}")
            LOGGER.exception("Extraction failed for mapping %s", mapping_id)
        # Recorded whatever the outcome. This was only assigned on the success path, so a mapping
        # that failed part-way through reported no warnings at all -- discarding, for instance, the
        # notice that records with no updated timestamp had been included in an incremental extract.
        if client is not None:
            mapping_manifest["warnings"] = client.warnings

        # Flushing the dump and writing the manifest are themselves S3 writes, so they can fail --
        # and they used to fail *outside* any handler, which aborted the whole run and left no
        # extract-manifest.json at all. Every mapping already extracted would then be unrecoverable
        # even though its records were staged. A failure here is recorded like any other and the run
        # carries on to write the manifest that describes it.
        try:
            dump_keys = preview_dump.close() if preview_dump is not None else []
            mapping_manifest["extractedRecords"] = [store.location(key) for key in dump_keys]
        except Exception as error:
            mapping_manifest["status"] = "FAILED"
            mapping_manifest["error"] = f"Could not write the extracted-records dump: {error}"
            mapping_manifest["traceback"] = traceback.format_exc()
            failures.append(f"{mapping_id}: {error}")
            LOGGER.exception("Writing the extracted-records dump failed for mapping %s", mapping_id)
        if preview_dump is None:
            mapping_manifest["extractedRecordsNote"] = (
                "not written because dumpExtractedRecords = false; the staged JSONL under "
                f"{mapping_prefix}/ is the record of what was extracted"
            )
        mapping_manifest["completedAt"] = utc_now()
        try:
            store.put_json(f"{mapping_prefix}/_manifest.json", mapping_manifest)
        except Exception as error:
            failures.append(f"{mapping_id}: could not write the mapping manifest: {error}")
            LOGGER.exception("Writing the mapping manifest failed for mapping %s", mapping_id)
        run_manifest["registries"].append(mapping_manifest)

    run_manifest["completedAt"] = utc_now()
    run_manifest["status"] = "FAILED" if failures else "SUCCEEDED"
    run_manifest["registryCount"] = len(mappings)
    run_manifest["recordCount"] = sum(int(item.get("recordCount", 0)) for item in run_manifest["registries"])
    run_manifest["failures"] = failures
    store.put_json(f"{run_prefix}/extract-manifest.json", run_manifest)
    extract_report = _build_extract_report(run_manifest, store, run_id)
    store.put_json(f"reports/run_id={run_id}/extract-summary.json", extract_report)
    # The same report as a page: the "should I load this?" decision, on its own, before any load
    # attempt exists to attach it to. report() and transform/load both reuse this exact HTML for
    # the extract-stage checks, so the page and the JSON can never disagree.
    store.put_text(
        f"reports/run_id={run_id}/extraction.html",
        report_html.render_extract_report(extract_report),
        content_type="text/html",
    )

    if failures:
        raise RuntimeError(f"Extract run {run_id} failed for {len(failures)} registry mappings")
    LOGGER.info("Extract run %s completed with %d records", run_id, run_manifest["recordCount"])


def _build_extract_report(
    run_manifest: dict[str, Any],
    store: Any,
    run_id: str,
) -> dict[str, Any]:
    """Summarize the run for reviewers: totals, per-registry counts, and the next step."""
    registries = run_manifest.get("registries", [])
    total_warnings = sum(len(r.get("warnings", [])) for r in registries)
    failed = [r for r in registries if r.get("status") != "SUCCEEDED"]
    status = run_manifest.get("status")
    ready = status == "SUCCEEDED"
    report_registries = [
        {
            "mappingId": r.get("mappingId"),
            "source": r.get("source"),
            "target": r.get("target"),
            "status": r.get("status"),
            "recordCount": r.get("recordCount", 0),
            "objectCount": r.get("objectCount", 0),
            "recordTypeCounts": r.get("recordTypeCounts", {}),
            "sourceStatusCounts": r.get("sourceStatusCounts", {}),
            "warnings": r.get("warnings", []),
            "error": r.get("error"),
            "rawPrefix": store.location(str(r.get("rawPrefix"))),
            # The cutoff this mapping actually read from and why, so a reviewer can see the window
            # without opening the raw manifest.
            "changedAfter": r.get("changedAfter"),
            "cutoffReason": r.get("cutoffReason"),
        }
        for r in registries
    ]
    # The incremental window, once, for the report page: every mapping in a run shares one load
    # mode, so this is the same changedAfter check a reviewer would otherwise repeat per registry.
    # None for a FULL load -- there is no window to state.
    incremental_window = None
    if str(run_manifest.get("load", {}).get("mode", "")).upper() == "INCREMENTAL":
        cutoffs = {r.get("changedAfter") for r in registries if r.get("changedAfter")}
        incremental_window = {
            "changedAfter": run_manifest.get("load", {}).get("changedAfter")
            or (min(cutoffs) if len(cutoffs) == 1 else None),
            "perRegistry": {str(r.get("mappingId")): r.get("changedAfter") for r in registries},
        }
    # How many records are past DRAFT at the source. The load stage reproduces those statuses on the
    # target records, so this is what to reconcile the load report's approval block against.
    past_draft = sum(
        count
        for registry in registries
        for status_name, count in registry.get("sourceStatusCounts", {}).items()
        if str(status_name).upper() not in {"DRAFT", "UNKNOWN"}
    )
    if ready:
        next_step = (
            "No target records are created during extraction. Review the record counts and the "
            "record-type distribution per registry below. When satisfied, start the transform/load "
            f"stage with RUN_ID={run_id}. If anything looks wrong, do not start transform/load; "
            "adjust the configuration and re-run extract with a new run id."
        )
        if past_draft:
            next_step += (
                f" Note: {past_draft} record(s) are past DRAFT in the Preview registry. Transform/load "
                "creates each record in DRAFT and then moves it to the status it holds at the source, "
                "so check the approval block of the load report to confirm it got there."
            )
    else:
        next_step = (
            "Extraction did not fully succeed. Review the per-registry errors and do not start "
            "transform/load until extraction reports SUCCEEDED."
        )
    return {
        "schemaVersion": 1,
        "stage": "EXTRACT",
        "runId": run_id,
        "status": status,
        "readyForTransform": ready,
        "startedAt": run_manifest.get("startedAt"),
        "completedAt": run_manifest.get("completedAt"),
        "load": run_manifest.get("load"),
        "totals": {
            "registries": run_manifest.get("registryCount", len(registries)),
            "records": run_manifest.get("recordCount", 0),
            "failedRegistries": len(failed),
            "warnings": total_warnings,
        },
        "incrementalWindow": incremental_window,
        "registries": report_registries,
        "rawDataLocation": store.location(f"runs/run_id={run_id}/raw/"),
        "extractManifest": store.location(f"runs/run_id={run_id}/extract-manifest.json"),
        # Mirrors the load report's artifacts map: every location this stage produced, with a
        # one-line explanation, so the extraction page is self-describing on its own. Only locations
        # that exist -- the record dump is listed when it was written, because pointing at an empty
        # prefix reads as a lost artifact rather than a disabled one.
        "artifacts": _extract_artifacts(
            store, run_id, dumped=any(registry.get("extractedRecords") for registry in registries)
        ),
        "nextStep": next_step,
    }


def _extract_artifacts(store: Any, run_id: str, *, dumped: bool) -> dict[str, str]:
    """Locations this stage produced, with a one-line explanation of each."""
    artifacts = {
        store.location(
            f"reports/run_id={run_id}/extraction.html"
        ): "This report as a page, with the checks to review before loading",
        store.location(f"reports/run_id={run_id}/extract-summary.json"): "The same report as data",
        store.location(
            f"runs/run_id={run_id}/extract-manifest.json"
        ): "The full per-registry extract manifest, with integrity metadata for every staged object",
    }
    if dumped:
        artifacts[store.location(f"reports/run_id={run_id}/extracted-records/")] = (
            "Every extracted Preview record, as described by the Preview API"
        )
    return artifacts


def _flush(
    store: S3Store,
    prefix: str,
    part_number: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    key = f"{prefix}/part-{part_number:05d}.jsonl"
    return store.put_json_lines(key, records)


def run() -> None:
    """Entrypoint wrapper used by the Glue shim: run extract, failing the job on error."""
    try:
        main()
    except Exception:
        LOGGER.exception("Extract job failed")
        sys.exit(1)


if __name__ == "__main__":
    run()
