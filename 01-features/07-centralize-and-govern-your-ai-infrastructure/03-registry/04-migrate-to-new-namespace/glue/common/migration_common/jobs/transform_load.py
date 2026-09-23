"""Transform/load job logic: map staged Preview records to the target registry and idempotently load them.

Before processing, it reconciles the staged objects against the extract manifest (run id,
per-object hash/size/count) and verifies the replay fingerprint so a run cannot be replayed
against changed transform/target logic; live writes additionally require the fingerprint to
match exactly. Each record is transformed, written to a transformed JSONL partition, and --
unless ``dryRun`` -- upserted into the target registry. It emits per-record detail rows plus a
run/attempt summary. Loading is gated behind manual approval: only ``dryRun`` runs and the
explicit live attempt reach this job.

Invoked by the ``glue/transform_load.py`` shim via :func:`run`.
"""

from __future__ import annotations

import csv
import functools
import io
import logging
import sys
import threading
import traceback
import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import boto3

from migration_common import report_html
from migration_common import watermark as watermark_state
from migration_common.aws_auth import invoker_for_endpoint
from migration_common.registry_api import (
    RegistryApiError,
    TargetNameClaims,
    TargetRegistryClient,
    validate_target_request,
)
from migration_common.settings import (
    parse_job_arguments,
    replay_configuration_fingerprint,
    resolve_configuration,
    resolve_run_id,
)
from migration_common.storage import JsonArrayWriter, S3Store
from migration_common.stores import resolve_store
from migration_common.transform import RecordTransformer
from migration_common.util import configure_logging, public_endpoint, safe_segment, utc_now

LOGGER = logging.getLogger("agent-registry-migration.transform-load")

# How often to report progress while loading. Matches the extract stage so a run's two halves read
# the same way in the log.
PROGRESS_EVERY_RECORDS = 100

# Keep summary.json and its self-contained HTML report useful without letting a systemic failure
# grow them without bound. The complete payload and traceback for every failure remain in the
# streamed failures artifact.
INLINE_FAILURE_DETAILS_LIMIT = 100
configure_logging()

USAGE = """\
Stage 2: transform staged Preview records to the target shape and load them into the target registries.

Use the CLI rather than this stage directly:

  agent-registry-migration run                    # dry run: transform and report, write nothing
  agent-registry-migration run --live             # create the target records
  agent-registry-migration run --live --resume <run-id>   # load an extract you already reviewed

The CLI translates your configuration into the arguments below, which are also what Glue passes
(Glue uses the --UPPER_SNAKE form; both styles work):

  --run-id                          run id produced by a successful extract (REQUIRED)
  --config-file / --config-prefix   where the configuration lives (a file, or an SSM prefix)
  --staging-bucket / --local-dir    where this run was staged
  --live true|false                 override the configured dryRun for this invocation only
  --attempt-id                      attempt label for the report (generated when omitted)

Nothing reaches a target registry unless --live true is passed or the configuration sets
dryRun = false. The default is a dry run.
"""

# How many staged objects to fetch ahead of the one being processed. One is enough to hide the S3
# GET behind record processing; more would buffer more data for no further gain.
STAGED_READ_AHEAD = 1


@dataclass
class RecordOutcome:
    """What happened to one staged record. Produced by a worker, aggregated by the main thread.

    Workers never touch shared state -- they return this and the main thread does all counting and
    writing, which keeps the concurrent path as easy to reason about as the sequential one.
    """

    mapping_id: str
    source_object: str
    old_record_id: str | None
    status: str
    processed_at: str
    action: str | None = None
    new_record_id: str | None = None
    name: str | None = None
    display_name: str | None = None
    record_type: str | None = None
    record_version: str | None = None
    primary_descriptor_type: str | None = None
    # The record's status in the Preview registry, and the status it has in the target registry after the write.
    # Reported side by side because they can still legitimately differ -- a status the target
    # registry's own approval policy overrides, or one that describes the source record's history.
    source_status: str | None = None
    target_status: str | None = None
    # What reproducing the source status took, and whether it worked. Empty actions mean nothing was
    # needed (the source record was DRAFT, so the created record already matched).
    status_actions: list[str] = field(default_factory=list)
    status_reproducible: bool = True
    status_error: str | None = None
    # The name the record had in Preview, kept alongside ``name`` for the crosswalk even when the
    # two are identical.
    preview_name: str | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    traceback_text: str | None = None
    preview_record: dict[str, Any] | None = None
    transformed_record: dict[str, Any] | None = None
    target_record: dict[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def status_matched(self) -> bool:
        """Whether the target record ended up in the status its Preview record holds.

        Only meaningful once the record has actually been written: a dry run has no target status to
        compare, and reports its expectations from the source side instead.
        """
        source = (self.source_status or "").strip().upper()
        target = (self.target_status or "").strip().upper()
        if not source or not target:
            return True
        return source == target

    @property
    def stranded_in_draft(self) -> bool:
        """Whether this record is past DRAFT at source but sitting in DRAFT in the target registry.

        This is the one status divergence that silently costs a customer something: a DRAFT record
        is not returned by data-plane search or the browsing APIs, so a record that was in service
        in Preview arrives undiscoverable. Answered per record, because the run-level number cannot
        be recovered from status *totals* -- one record auto-approved out of DRAFT cancels out one
        record stranded in it, and the subtraction then reports zero while a record is stranded.
        """
        source = (self.source_status or "").strip().upper()
        target = (self.target_status or "").strip().upper()
        if not source or source in {"DRAFT", "UNKNOWN"}:
            return False
        return target == "DRAFT"


def _target_identity(target: dict[str, Any]) -> tuple[str, str, str]:
    """Return the canonical account/Region/registry identity independent of access credentials."""
    return (
        str(target.get("accountId", "")),
        str(target["region"]),
        str(target.get("registryId", "")),
    )


def _source_claimant_id(mapping: dict[str, Any], record_id: str) -> str:
    """Return a source-record identity that remains unique across consolidated mappings."""
    source = mapping["source"]
    return "/".join(
        (
            str(source["accountId"]),
            str(source["region"]),
            str(source["registryId"]),
            record_id,
        )
    )


class TargetClientPool:
    """Thread-safe cache of target clients, one per distinct target access route.

    botocore clients are safe to call from many threads, but building one (which may assume a
    role) is not, so construction happens under a lock and is then shared by all workers. Clients
    using different credentials for the same canonical target share the final name and resolved-
    record claim guards.
    """

    def __init__(self, api_config: dict[str, Any], run_id: str) -> None:
        self._api_config = api_config
        self._run_id = run_id
        self._clients: dict[tuple[str, str, str, str, str], TargetRegistryClient] = {}
        self._name_claims: dict[tuple[str, str, str], TargetNameClaims] = {}
        self._target_claims: dict[tuple[str, str, str], tuple[dict[tuple[str, str], str], threading.Lock]] = {}
        self._lock = threading.Lock()

    def for_target(self, target: dict[str, Any]) -> TargetRegistryClient:
        target_key = _target_identity(target)
        client_key = target_key + (
            str(target.get("roleArn") or ""),
            str(target.get("externalId", "")),
        )
        with self._lock:
            client = self._clients.get(client_key)
            if client is None:
                claims = self._name_claims.get(target_key)
                if claims is None:
                    claims = TargetNameClaims()
                    self._name_claims[target_key] = claims
                target_claims = self._target_claims.get(target_key)
                if target_claims is None:
                    target_claims = ({}, threading.Lock())
                    self._target_claims[target_key] = target_claims
                client = TargetRegistryClient(
                    invoker_for_endpoint(target, self._run_id, "load"),
                    self._api_config,
                    str(target["region"]),
                )
                bind_claim_guards = getattr(client, "_bind_claim_guards", None)
                if callable(bind_claim_guards):
                    bind_claim_guards(claims, target_claims[0], target_claims[1])
                self._clients[client_key] = client
            return client


class TargetNameClaimPool:
    """Target-name claims coordinated in deterministic staged-input order.

    Every canonical target gets one claim set regardless of which role or external ID accesses
    it. The sequence coordinator preserves staged order while workers transform concurrently, so
    local, Glue, dry-run, and live attempts choose the same claimant.
    """

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str, str], TargetNameClaims] = {}
        self._lock = threading.Lock()
        self._sequence = threading.Condition()
        self._next_sequence = 0
        self._skipped_sequences: set[int] = set()

    def for_target(self, target: dict[str, Any]) -> TargetNameClaims:
        key = _target_identity(target)
        with self._lock:
            claims = self._claims.get(key)
            if claims is None:
                claims = TargetNameClaims()
                self._claims[key] = claims
            return claims

    def claim_in_order(
        self,
        sequence: int,
        target: dict[str, Any],
        name: str,
        record_version: Any,
        source_record_id: str,
    ) -> None:
        """Claim one identity when its staged-input turn arrives, then release the next turn."""
        with self._sequence:
            while sequence != self._next_sequence:
                self._sequence.wait()
            try:
                self.for_target(target).claim(str(target["registryId"]), name, record_version, source_record_id)
            finally:
                self._next_sequence += 1
                self._advance_past_skipped()
                self._sequence.notify_all()

    def skip(self, sequence: int) -> None:
        """Release a sequence whose record failed before reaching the identity claim."""
        with self._sequence:
            if sequence < self._next_sequence:
                return
            self._skipped_sequences.add(sequence)
            self._advance_past_skipped()
            self._sequence.notify_all()

    def _advance_past_skipped(self) -> None:
        while self._next_sequence in self._skipped_sequences:
            self._skipped_sequences.remove(self._next_sequence)
            self._next_sequence += 1


def _process_record(
    source_key: str,
    envelope: dict[str, Any],
    *,
    mapping_by_id: dict[str, dict[str, Any]],
    transformer: RecordTransformer,
    clients: TargetClientPool | None,
    dry_run: bool,
    name_claims: TargetNameClaimPool | None = None,
    claim_sequence: int | None = None,
    match_source_status: bool = True,
    known_record_ids: dict[str, dict[str, str]] | None = None,
) -> RecordOutcome:
    """Transform one staged record and (unless ``dry_run``) upsert it into its target registry.

    Returns an outcome instead of raising, so one bad record never aborts the batch; the caller
    decides whether the run fails.
    """
    mapping_id = str(envelope.get("mappingId", ""))
    old_record_id = _old_record_id(envelope)
    outcome = RecordOutcome(
        mapping_id=mapping_id,
        source_object=source_key,
        old_record_id=old_record_id,
        status="FAILED",
        processed_at=utc_now(),
    )
    try:
        if not old_record_id:
            raise ValueError(
                "Staged record has no normalized oldRecordId; loading is blocked because "
                "the required old-to-new ID mapping cannot be produced"
            )
        current_mapping = mapping_by_id[mapping_id]
        _verify_mapping_has_not_changed(envelope, current_mapping)
        preview_record = envelope.get("record")
        if not isinstance(preview_record, dict):
            raise ValueError("Staged envelope record must be an object")

        transform_context = dict(current_mapping)
        transform_context["oldRecordId"] = old_record_id
        transformed = transformer.transform(preview_record, transform_context)
        outcome.preview_record = preview_record
        outcome.transformed_record = transformed.record
        outcome.old_record_id = transformed.old_record_id
        outcome.name = transformed.record["name"]
        outcome.display_name = transformed.record["displayName"]
        outcome.record_type = transformed.record["recordType"]
        outcome.record_version = transformed.record.get("recordVersion")
        outcome.primary_descriptor_type = next(iter(transformed.record["descriptors"]))
        outcome.source_status = transformed.source_status
        outcome.warnings = list(transformed.warnings)
        outcome.preview_name = transformed.preview_name

        # Apply the target request contract on every path, so a dry run cannot pass a record that the
        # live load would reject. This is the check the service itself would fail on, and the target
        # model types `descriptors` as a document, so botocore will not catch it.
        validate_target_request(transformed.record)

        target = current_mapping["target"]
        source_claimant_id = _source_claimant_id(current_mapping, transformed.old_record_id)
        # Reserve transformed identities in staged-input order before dry-run acceptance or any
        # live API call. This tiny ordered section makes collision ownership deterministic while
        # transformation, service calls, and status polling remain concurrent. Canonical source
        # identity prevents equal record IDs from different Preview registries being mistaken for
        # an idempotent replay. The live client repeats the claim immediately before lookup/write.
        if name_claims is not None and claim_sequence is not None:
            name_claims.claim_in_order(
                claim_sequence,
                target,
                str(transformed.record["name"]),
                transformed.record.get("recordVersion"),
                source_claimant_id,
            )
        elif dry_run or clients is None:
            # Direct single-record callers do not need sequence coordination, but still need the
            # same no-client validation on a dry run.
            (name_claims or TargetNameClaimPool()).for_target(target).claim(
                str(target["registryId"]),
                str(transformed.record["name"]),
                transformed.record.get("recordVersion"),
                source_claimant_id,
            )

        if dry_run or clients is None:
            outcome.action = "dryRun"
        else:
            client = clients.for_target(target)
            load_result = client.upsert(
                registry_id=str(target["registryId"]),
                record=transformed.record,
                # The canonical source identity lets client-level name and resolved-record guards
                # distinguish equal record IDs from different Preview registries.
                source_record_id=source_claimant_id,
                # The target record an earlier run migrated this same source record to, if any. Matched
                # ahead of the name, so a record renamed in Preview updates the target record it already
                # has instead of being migrated a second time under its new name.
                known_record_id=(known_record_ids or {}).get(mapping_id, {}).get(outcome.old_record_id or ""),
            )
            outcome.warnings.extend(load_result.warnings)
            outcome.action = load_result.action
            outcome.new_record_id = load_result.new_record_id
            # Described target record, captured from the status poll upsert already performs.
            outcome.target_record = load_result.record
            if isinstance(load_result.record, dict):
                outcome.target_status = load_result.record.get("status")
            if not outcome.new_record_id:
                raise RuntimeError(
                    "The target API did not return a recordId, so the required old-to-new ID mapping could not be produced"
                )
            if match_source_status:
                _apply_source_status(outcome, client, str(target["registryId"]))
        outcome.status = "SUCCEEDED"
    except Exception as error:  # noqa: BLE001 - reported per record, not raised
        outcome.error = str(error)
        outcome.traceback_text = traceback.format_exc()
        # A create can succeed and the record still fail to settle (CREATE_FAILED, or a timeout).
        # That record exists in the target registry, so the crosswalk has to name it even though
        # this row is a failure -- otherwise the only way to find it is to read the error text.
        if isinstance(error, RegistryApiError) and error.record_id and not outcome.new_record_id:
            outcome.new_record_id = error.record_id
        LOGGER.warning(
            "Transform/load failed for mapping %s, old record %s: %s",
            mapping_id,
            old_record_id,
            error,
        )
    finally:
        # A transform/validation failure can happen before this worker reaches its ordered claim.
        # Mark that sequence complete so later workers cannot wait forever. If the claim already
        # ran, ``skip`` sees an earlier sequence and is a no-op.
        if name_claims is not None and claim_sequence is not None:
            name_claims.skip(claim_sequence)
    return outcome


def _apply_source_status(
    outcome: RecordOutcome,
    client: TargetRegistryClient,
    registry_id: str,
) -> None:
    """Put the loaded target record into the status its Preview record holds, and record the outcome.

    A record that was APPROVED in Preview is invisible to the target registry data plane while it sits in DRAFT,
    so this is part of migrating it. Never raises: the record is already loaded and correct, and a
    refused status transition is reported rather than turned into a failed record.
    """
    source_status = (outcome.source_status or "").strip().upper()
    if not source_status or not outcome.new_record_id:
        return
    result = client.apply_status(
        registry_id=registry_id,
        record_id=outcome.new_record_id,
        desired_status=source_status,
        current_status=str(outcome.target_status) if outcome.target_status else None,
        reason=f"Migrated from Preview record {outcome.old_record_id} in status {source_status}",
    )
    outcome.status_actions = list(result.actions)
    outcome.status_reproducible = result.reproducible
    outcome.status_error = result.error
    if result.achieved:
        outcome.target_status = result.achieved
    if not result.reproducible:
        outcome.warnings.append(
            f"Preview status {source_status} describes the source record's own history and cannot be "
            "reproduced on a new target record; it was left in DRAFT."
        )
    elif result.error:
        outcome.warnings.append(
            f"Could not put the target record into its Preview status {source_status}: {result.error} "
            f"(it is {outcome.target_status or 'unknown'}). The record itself loaded correctly."
        )
    elif result.achieved and result.achieved != source_status:
        outcome.warnings.append(
            f"Target record is {result.achieved}, not the Preview status {source_status}: the target "
            "registry's own approval policy decided the final state."
        )


def _iter_batches(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    """Yield fixed-size batches, so only one batch is ever held in memory."""
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _iter_outcomes(
    staged_records: Iterable[tuple[str, dict[str, Any]]],
    worker: Any,
    *,
    concurrency: int,
) -> Iterator[RecordOutcome]:
    """Run ``worker`` over staged records in parallel while yielding results in input order.

    The per-record cost is almost entirely waiting on the target API (create, then poll until the
    record settles), so threads -- not processes -- overlap that waiting without extra capacity.
    ``executor.map`` preserves report order. The production worker carries an internal marker that
    asks this helper to pass the staged-input position used to serialize only the duplicate claim;
    ordinary two-argument workers keep the existing helper contract.

    If the caller stops consuming, the pool cancels work that has not started so a live load does
    not keep creating records after the run has already decided to abort.
    """
    with_sequence = bool(getattr(worker, "_receives_claim_sequence", False))

    def invoke(sequence: int, item: tuple[str, dict[str, Any]]) -> RecordOutcome:
        if with_sequence:
            return worker(item[0], item[1], claim_sequence=sequence)
        return worker(item[0], item[1])

    enumerated = enumerate(staged_records)
    if concurrency <= 1:
        for sequence, item in enumerated:
            yield invoke(sequence, item)
        return

    executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="load")
    try:
        # A window of a few multiples of the worker count keeps every thread busy while bounding
        # how many records (and their payloads) are resident at once.
        for batch in _iter_batches(enumerated, concurrency * 4):
            yield from executor.map(lambda entry: invoke(entry[0], entry[1]), batch)
    finally:
        # cancel_futures is Python 3.8+, so it is available on the Glue 3.9 runtime. Records already
        # running still finish -- a thread cannot be interrupted mid-create, and abandoning one
        # half-way would be worse than completing it -- but nothing further is started.
        executor.shutdown(wait=True, cancel_futures=True)


def main(argv: list[str] | None = None) -> None:
    """Transform and (unless dryRun) load every staged record, then write the attempt report.

    ``argv`` defaults to the process arguments; passing it lets the one-command orchestrator
    invoke this stage in-process.
    """
    arguments = parse_job_arguments(argv)
    if "help" in arguments or "h" in arguments:
        print(USAGE)
        return
    run_id = resolve_run_id(arguments, allow_generate=False)
    attempt_id = _resolve_attempt_id(arguments)
    # Configuration first: it carries the staging bucket the deployment created (see
    # resolve_staging_bucket), so --staging-bucket is only needed to point elsewhere.
    settings, mappings, _config_source = resolve_configuration(arguments)
    store, _staging_location = resolve_store(arguments, settings, boto3_module=boto3)
    mapping_by_id = {str(mapping["id"]): mapping for mapping in mappings}
    run_prefix = f"runs/run_id={run_id}"
    extract_manifest = store.get_json(f"{run_prefix}/extract-manifest.json")
    if extract_manifest.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Extract manifest for run {run_id} is not successful: {extract_manifest.get('status')}")

    load = settings["load"]
    dry_run = bool(load.get("dryRun", True))
    configured_drift = bool(load.get("allowReplayConfigurationDrift", False))
    replay_configuration = _validate_replay_configuration(
        extract_manifest,
        settings,
        # Drift can be inspected in a dry run, but live writes must always remain bound to
        # the exact code+adapter fingerprint recorded by extraction.
        allow_drift=configured_drift if dry_run else False,
    )
    raw_objects = _validate_extract_manifest(store, extract_manifest, run_id)
    # Every staged record is always processed regardless of this flag -- one record's failure never
    # stops the batch. It only decides what happens once every record has been tried: false (the
    # default) reports every failure and exits 0 so the run is not treated as broken; true fails the
    # run (nonzero exit, report status FAILED) for estates that want a load to be all-or-nothing.
    fail_on_error = bool(load.get("failOnRecordError", False))
    records_per_object = int(load.get("recordsPerObject", 500))
    concurrency = int(load.get("loadConcurrency", 32))
    match_source_status = bool(load.get("matchSourceStatus", True))
    transformer = RecordTransformer(settings["transform"])
    report_root = f"reports/run_id={run_id}/attempt={attempt_id}"
    started_at = utc_now()
    # Attempt lock lives with the engine's internal state, not in reports/, so the report folder
    # contains only artifacts a person would want to open.
    store.put_json_if_absent(
        f"state/locks/run_id={run_id}/transform-load-attempt={attempt_id}.json",
        {
            "runId": run_id,
            "attemptId": attempt_id,
            "stage": "TRANSFORM_LOAD",
            "createdAt": started_at,
        },
    )
    clients = None if dry_run else TargetClientPool(settings["api"]["target"], run_id)
    # Shared by every worker in both modes. It orders only the in-memory identity reservation by
    # staged position; all transformation and target work remains concurrent. Dry-run uses no client,
    # while live clients retain their own identical final pre-write guard.
    name_claims = TargetNameClaimPool()
    # What every previous live load of these mappings produced: source recordId -> target recordId. Read
    # once up front (it is one small object per mapping) and consulted per record, so a record that
    # was renamed in Preview since it was last migrated is still recognised as the same record.
    known_record_ids = {mapping_id: watermark_state.read_idmap(store, mapping_id) for mapping_id in mapping_by_id}
    for mapping_id, known in known_record_ids.items():
        if known:
            LOGGER.info(
                "Mapping %s: %d record(s) previously migrated, from %s",
                mapping_id,
                len(known),
                store.location(watermark_state.idmap_key(mapping_id)),
            )
    summaries = _initialize_summaries(extract_manifest, mapping_by_id)
    # Per-mapping old->new id crosswalk rows, accumulated in memory and written as one CSV per
    # registry at the end (customers need this to repoint dependencies that referenced a preview
    # recordId). Rows are small; only the CSV columns are held, not the record payloads.
    crosswalk_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Failures only. Successes are fully described in the comparison dump, so repeating every
    # record here would just add noise to the report.
    #
    # Streamed in bounded chunks like the comparison dump, not accumulated: a failure row carries
    # the full Preview and transformed payloads, and the run does not stop at the first failure
    # (failOnRecordError defaults to false). A systemic problem -- the wrong role, the wrong region --
    # therefore fails every record, and buffering every payload is how the job runs out of memory
    # on the exact run that most needs its report written.
    failure_writers: dict[str, JsonArrayWriter] = {}
    # Per-mapping side-by-side dumps: Preview record as extracted, the transformed target payload, and
    # the target record as the service describes it after the write. Written in bounded chunks so the
    # artifact stays diffable against the extract's dump without buffering everything.
    comparison_writers: dict[str, JsonArrayWriter] = {}

    def comparison_writer(mapping: str) -> JsonArrayWriter:
        if mapping not in comparison_writers:
            comparison_writers[mapping] = JsonArrayWriter(
                store,
                f"{report_root}/record-comparison/mapping={safe_segment(mapping)}",
                basename="part",
                chunk_size=records_per_object,
            )
        return comparison_writers[mapping]

    def failure_writer(mapping: str) -> JsonArrayWriter:
        """Writer for one mapping's failure rows, created only if that mapping has a failure.

        Same layout as record-comparison, so the two artifacts are read the same way, and nothing
        at all is written for a mapping that had no failures.
        """
        if mapping not in failure_writers:
            failure_writers[mapping] = JsonArrayWriter(
                store,
                f"{report_root}/failures/mapping={safe_segment(mapping)}",
                basename="part",
                chunk_size=records_per_object,
            )
        return failure_writers[mapping]

    total_errors = 0
    processed_records = 0
    # What extraction staged, so progress can be reported as a position rather than a bare count.
    # Reconciled exactly at the end of the run; here it is only used for the log line.
    staged_record_count = int(extract_manifest.get("recordCount", 0))
    worker = functools.partial(
        _process_record,
        mapping_by_id=mapping_by_id,
        transformer=transformer,
        name_claims=name_claims,
        clients=clients,
        dry_run=dry_run,
        match_source_status=match_source_status,
        known_record_ids=known_record_ids,
    )
    worker._receives_claim_sequence = True

    LOGGER.info(
        "Starting transform/load run %s attempt %s (dryRun=%s, concurrency=%d)",
        run_id,
        attempt_id,
        dry_run,
        concurrency,
    )
    try:
        # Fetch the next staged object while the current one is being loaded, so a multi-object
        # run does not pause on an S3 GET between batches of records. Order is unaffected.
        staged_records = store.iter_json_lines_objects(raw_objects, read_ahead=STAGED_READ_AHEAD)
        for outcome in _iter_outcomes(staged_records, worker, concurrency=concurrency):
            processed_records += 1
            if processed_records % PROGRESS_EVERY_RECORDS == 0:
                # Each record is a target write plus status polling, so a large run is a long quiet
                # stretch. The staged total is known from the extract manifest, which makes this a
                # real position rather than just a sign of life.
                LOGGER.info(
                    "%s %d of %d staged records (%d error(s) so far)",
                    "Checked" if dry_run else "Loaded",
                    processed_records,
                    staged_record_count,
                    total_errors,
                )
            summary = summaries.get(outcome.mapping_id)
            if summary is None:
                raise RuntimeError(f"Staged record references unknown mapping {outcome.mapping_id!r}")

            if outcome.succeeded:
                summary["transformed"] += 1
                summary["warningCount"] += len(outcome.warnings)
                # ``.get`` rather than ``+= 1``: an action the summary does not pre-seed must show
                # up as its own counter, not crash the run after every record has been written.
                action_key = str(outcome.action)
                summary[action_key] = int(summary.get(action_key, 0)) + 1
                # Exact per-record mapping with both sides described, for verification.
                summary["sourceStatusCounts"][outcome.source_status or "UNKNOWN"] = (
                    summary["sourceStatusCounts"].get(outcome.source_status or "UNKNOWN", 0) + 1
                )
                if outcome.target_status:
                    summary["targetStatusCounts"][outcome.target_status] = (
                        summary["targetStatusCounts"].get(outcome.target_status, 0) + 1
                    )
                if outcome.status_actions:
                    summary["statusesApplied"] = int(summary.get("statusesApplied", 0)) + 1
                if outcome.status_error or not outcome.status_reproducible:
                    summary["statusesNotApplied"] = int(summary.get("statusesNotApplied", 0)) + 1
                # Counted per record rather than derived from the status totals afterwards: the
                # totals cannot express which record ended up where, so a record auto-approved out
                # of DRAFT hid a record stranded in it. See RecordOutcome.stranded_in_draft.
                if outcome.stranded_in_draft:
                    summary["recordsStrandedInDraft"] = int(summary.get("recordsStrandedInDraft", 0)) + 1
                if not outcome.status_matched:
                    summary["statusMismatched"] = int(summary.get("statusMismatched", 0)) + 1
                comparison_writer(outcome.mapping_id).append(
                    {
                        "oldRecordId": outcome.old_record_id,
                        "newRecordId": outcome.new_record_id,
                        "name": outcome.name,
                        "previewName": outcome.preview_name,
                        "displayName": outcome.display_name,
                        "recordType": outcome.record_type,
                        "recordVersion": outcome.record_version,
                        "action": outcome.action,
                        "sourceStatus": outcome.source_status,
                        "targetStatus": outcome.target_status,
                        "statusActions": outcome.status_actions,
                        "statusError": outcome.status_error,
                        "warnings": outcome.warnings,
                        "previewRecord": outcome.preview_record,
                        "transformedRecord": outcome.transformed_record,
                        "targetRecord": outcome.target_record,
                    }
                )
            else:
                total_errors += 1
                summary["failed"] += 1
                # Put a compact, bounded reason in summary.json so both local and Glue HTML reports
                # can explain failures without fetching another artifact. Full payloads and
                # tracebacks remain in the streamed artifact below.
                failure_details = summary["failureDetails"]
                if len(failure_details) < INLINE_FAILURE_DETAILS_LIMIT:
                    failure_details.append(
                        {
                            "oldRecordId": outcome.old_record_id,
                            "newRecordId": outcome.new_record_id,
                            "name": outcome.name,
                            "recordType": outcome.record_type,
                            "error": outcome.error,
                        }
                    )
                failure_writer(outcome.mapping_id).append(
                    {
                        "oldRecordId": outcome.old_record_id,
                        # Set when the record was created and then failed to settle, so the row
                        # names the record left behind in the target registry. Null when the write
                        # never happened, which is the common case.
                        "newRecordId": outcome.new_record_id,
                        "name": outcome.name,
                        "recordType": outcome.record_type,
                        "sourceObject": outcome.source_object,
                        "processedAt": outcome.processed_at,
                        "error": outcome.error,
                        "traceback": outcome.traceback_text,
                        "previewRecord": outcome.preview_record,
                        "transformedRecord": outcome.transformed_record,
                    }
                )

            crosswalk_rows[outcome.mapping_id].append(
                {
                    "oldRecordId": outcome.old_record_id or "",
                    "newRecordId": outcome.new_record_id or "",
                    "previewName": outcome.preview_name or "",
                    "name": outcome.name or "",
                    "displayName": outcome.display_name or "",
                    "recordType": outcome.record_type or "",
                    "recordVersion": outcome.record_version or "",
                    "action": outcome.action or ("failed" if not outcome.succeeded else ""),
                    "status": outcome.status,
                    "targetStatus": outcome.target_status or "",
                }
            )
    finally:
        for writer in (*comparison_writers.values(), *failure_writers.values()):
            writer.close()

    for mapping_id, writer in comparison_writers.items():
        if mapping_id in summaries:
            summaries[mapping_id]["recordComparison"] = [store.location(key) for key in writer.keys]
    # One failures artifact per affected mapping, and nothing at all for a mapping that had none.
    for mapping_id, writer in failure_writers.items():
        if mapping_id in summaries:
            summaries[mapping_id]["failures"] = [store.location(key) for key in writer.keys]

    crosswalk_prefix = f"{report_root}/id-crosswalk"
    crosswalk_locations = _write_crosswalks(store, crosswalk_prefix, summaries, crosswalk_rows)
    for mapping_id, location in crosswalk_locations.items():
        summaries[mapping_id]["idCrosswalk"] = location

    # Before the reconciliation check below, deliberately: every id in here names a record that is
    # already in the target registry, and a run that ends in an exception must not leave them unrecorded
    # -- the next run would then create a second copy of each one.
    _commit_id_maps(store, summaries, crosswalk_rows, run_id=run_id, dry_run=dry_run)

    # Reconcile against the extract manifest BEFORE touching the watermarks. If the counts do not
    # agree we cannot trust that every staged record was seen, and advancing a watermark on an
    # unreconciled run would permanently skip whatever was missed.
    #
    # Recorded rather than raised here: this used to raise immediately, which left the crosswalks,
    # the failure rows and the id maps written but no summary.json or summary.html to read them
    # from -- a half-populated report directory and no statement of what went wrong. The run still
    # fails, at the end of this function, after the report exists.
    expected_record_count = int(extract_manifest.get("recordCount", -1))
    reconciliation_error: str | None = None
    if processed_records != expected_record_count:
        reconciliation_error = (
            f"Processed {processed_records} records but extract manifest declares {expected_record_count}"
        )
        LOGGER.error(
            "Staged record reconciliation failed for run %s attempt %s: %s",
            run_id,
            attempt_id,
            reconciliation_error,
        )

    # Commit incremental watermarks. Only for a live run, only for mappings whose records all
    # loaded, and never on an unreconciled run: advancing the watermark after a partial failure
    # would permanently skip the records that failed. A dry run never advances it, because nothing
    # reached the target registry.
    if reconciliation_error is None:
        _commit_watermarks(
            store,
            extract_manifest,
            summaries,
            run_id=run_id,
            attempt_id=attempt_id,
            dry_run=dry_run,
        )
    else:
        for summary in summaries.values():
            summary["watermarkCommitted"] = False
            summary["watermarkSkipReason"] = (
                "staged record reconciliation failed, so the watermark stays put: the next "
                "incremental run must re-read this window"
            )

    completed_at = utc_now()
    report_status = (
        "FAILED"
        if reconciliation_error or (total_errors and fail_on_error)
        else "PARTIAL_SUCCESS"
        if total_errors
        else "SUCCEEDED"
    )
    report = {
        "schemaVersion": 1,
        "runId": run_id,
        "attemptId": attempt_id,
        "stage": "TRANSFORM_LOAD",
        "status": report_status,
        "dryRun": dry_run,
        "replayConfiguration": replay_configuration,
        "startedAt": started_at,
        "completedAt": completed_at,
        "concurrency": concurrency,
        "errorCount": total_errors,
        "processedRecordCount": processed_records,
        # Whether every record extraction staged was actually seen by this attempt. A mismatch means
        # the run cannot be trusted to have covered the window, so no watermark advances and the
        # status above is FAILED -- stated here so the report says which of the two kinds of failure
        # this was, rather than leaving it to the log.
        "reconciliation": {
            "expectedRecordCount": expected_record_count,
            "processedRecordCount": processed_records,
            "matches": reconciliation_error is None,
            "error": reconciliation_error,
        },
        # A map of every artifact this run produced, with a one-line explanation, so the report is
        # self-describing and nobody has to guess what a folder holds.
        "artifacts": {
            store.location(f"{report_root}/summary.html"): "This report as a page, with the checks to review",
            store.location(f"{report_root}/summary.json"): "The same report as data",
            store.location(
                f"reports/run_id={run_id}/extract-summary.json"
            ): "What extraction read from the Preview registries",
            store.location(
                f"reports/run_id={run_id}/extracted-records/"
            ): "Every extracted Preview record, as described by the Preview API",
            store.location(f"{crosswalk_prefix}/"): "CSV mapping each Preview recordId to its new target recordId",
            store.location(
                f"{report_root}/record-comparison/"
            ): "Per record: Preview record, transformed payload, and the resulting target record",
            store.location(
                f"{report_root}/failures/"
            ): "Records that failed, with the error and traceback (absent when none failed)",
        },
        "approval": _approval_summary(summaries, dry_run=dry_run, match_source_status=match_source_status),
        "registries": list(summaries.values()),
    }
    store.put_json(f"{report_root}/summary.json", report)
    # The same report as a page. Verifying a migration from the JSON means knowing which fields
    # matter and what each should say; the page answers each of those questions for this run, so it
    # is what to send to whoever signs the migration off.
    store.put_text(
        f"{report_root}/summary.html",
        report_html.render_report(
            report,
            store.get_json_if_present(f"reports/run_id={run_id}/extract-summary.json"),
        ),
        content_type="text/html",
    )

    LOGGER.info(
        "Transform/load run %s attempt %s completed: status=%s errors=%d",
        run_id,
        attempt_id,
        report["status"],
        total_errors,
    )
    # A status that could not be reproduced leaves a loaded record in the wrong state, which is not
    # a record failure and so never appears in errors. Logged at WARNING because it is the one
    # outcome a clean-looking run can still hide, and in Glue the log is all there is to look at.
    statuses_not_applied = int(report["approval"].get("statusesNotApplied", 0))
    if statuses_not_applied:
        LOGGER.warning(
            "%d record(s) loaded but could not be put into their Preview status; they are listed "
            "per record as statusError in %s",
            statuses_not_applied,
            store.location(f"{report_root}/record-comparison/"),
        )
    # Raised only now, with the report on disk: a reconciliation failure is the one case where the
    # report matters most, because the numbers in it are how you work out what was missed.
    if reconciliation_error:
        raise RuntimeError(
            f"{reconciliation_error}. The attempt report was still written: "
            f"{store.location(f'{report_root}/summary.json')}"
        )
    if total_errors and fail_on_error:
        raise RuntimeError(f"Transform/load run {run_id} attempt {attempt_id} failed for {total_errors} records")


def _approval_summary(
    summaries: dict[str, dict[str, Any]],
    *,
    dry_run: bool,
    match_source_status: bool = True,
) -> dict[str, Any]:
    """Reconcile source approval state against the target registry state for the whole run.

    target creates every record in DRAFT, so the load stage drives each record to the status its Preview
    record holds. This states the result in numbers: how many statuses were applied, how many the
    tool could not reproduce, and any record left in a status other than its source's -- because an
    approved record still sitting in DRAFT is invisible to data-plane search and browsing.
    """
    source_counts: dict[str, int] = {}
    target_counts: dict[str, int] = {}
    applied = 0
    not_applied = 0
    stranded_in_draft = 0
    mismatched = 0
    for summary in summaries.values():
        for status, count in summary.get("sourceStatusCounts", {}).items():
            source_counts[status] = source_counts.get(status, 0) + int(count)
        for status, count in summary.get("targetStatusCounts", {}).items():
            target_counts[status] = target_counts.get(status, 0) + int(count)
        applied += int(summary.get("statusesApplied", 0))
        not_applied += int(summary.get("statusesNotApplied", 0))
        stranded_in_draft += int(summary.get("recordsStrandedInDraft", 0))
        mismatched += int(summary.get("statusMismatched", 0))

    not_draft_at_source = sum(
        count for status, count in source_counts.items() if status.upper() not in {"DRAFT", "UNKNOWN"}
    )
    # `stranded_in_draft` and `mismatched` are summed from per-record decisions made during the run
    # (see RecordOutcome.stranded_in_draft), NOT derived from the status totals above. Subtracting
    # totals -- which is what this used to do -- cannot tell which record ended up where: one record
    # the registry auto-approved out of DRAFT cancelled out one record stranded in DRAFT, so the
    # headline number read zero while an approved record sat invisible to data-plane search. The
    # totals are still reported, because side-by-side counts are what a reviewer scans.

    if dry_run:
        note = (
            "Dry run: nothing was written. On a live load each record is created in DRAFT and then "
            "moved to the status it holds in the Preview registry."
            if match_source_status
            else "Dry run: nothing was written. Status matching is off, so a live load would leave "
            "every record in DRAFT."
        )
    elif not match_source_status:
        note = (
            f"Status matching is off (runtime.load.matchSourceStatus = false), so all {not_draft_at_source} "
            "record(s) that were past DRAFT in the Preview registry are DRAFT in the target registry. Data-plane search "
            "and the browsing APIs will not return them until they are submitted for approval."
        )
    elif stranded_in_draft:
        # Checked before `not_applied`, because this is the outcome that costs the customer
        # something whether or not a transition was refused: a record that was in service in
        # Preview is in the target registry but cannot be found in it.
        note = (
            f"{stranded_in_draft} record(s) were past DRAFT in the Preview registry but are DRAFT in "
            "the target registry, so data-plane search and the browsing APIs will not return them. They are listed "
            "per record in record-comparison/ with their sourceStatus, targetStatus and statusError. "
            "Submit them for approval in the target registry to finish the migration."
            + (f" {applied} other record(s) did reach their Preview status." if applied else "")
        )
    elif not_applied:
        note = (
            f"{applied} record(s) were moved to their Preview status; {not_applied} could not be. "
            "Statuses that describe the source record's own history (CREATE_FAILED, UPDATE_FAILED) "
            "cannot exist on a new record; anything else is a refused transition, listed per record "
            "in record-comparison/ as statusError. Records left in DRAFT are not returned by "
            "data-plane search or the browsing APIs."
        )
    elif mismatched:
        # Not DRAFT, but not the source status either -- the target registry's approval policy
        # decided a different end state. Worth stating rather than folding into "every check clear".
        note = (
            f"{mismatched} record(s) hold a target status other than their Preview status, though none "
            "are stranded in DRAFT, so all of them are discoverable. The target registry's own "
            "approval policy decided the final state; see sourceStatus and targetStatus per record in "
            "record-comparison/."
        )
    elif applied:
        note = (
            f"{applied} record(s) were moved to the status they hold in the Preview registry; "
            "the rest were DRAFT at source and needed no change."
        )
    elif not_draft_at_source:
        # Nothing to apply, but not because the source was all DRAFT: these records already held
        # their source status, which is what a re-run of an already-migrated registry looks like.
        note = (
            f"No status change was needed: {not_draft_at_source} record(s) are past DRAFT at source "
            "and already hold that status in the target registry."
        )
    else:
        note = "Every record was DRAFT in the Preview registry, so no status change was needed."
    return {
        "matchSourceStatus": match_source_status,
        "sourceStatusCounts": source_counts,
        "targetStatusCounts": target_counts,
        "statusesApplied": applied,
        "statusesNotApplied": not_applied,
        # Kept under its original name: it is what a reviewer looks for, and it still answers the
        # same question -- how many records need a human to finish their approval in the target registry. What
        # changed is how it is derived: a per-record count, not a subtraction of status totals.
        "recordsNeedingResubmission": stranded_in_draft if match_source_status else not_draft_at_source,
        # Every record whose target status differs from its Preview status, stranded or not. Reported
        # alongside the count above so "discoverable but in a different state" is distinguishable
        # from "invisible", which are two different things to act on.
        "statusMismatched": mismatched,
        "note": note,
    }


def _validate_replay_configuration(
    extract_manifest: dict[str, Any],
    settings: dict[str, Any],
    *,
    allow_drift: bool,
) -> dict[str, Any]:
    """Compare the extract-time fingerprint to the current one; block live drift unless allowed."""
    declared = extract_manifest.get("replayConfiguration")
    current_sha256 = replay_configuration_fingerprint(settings)
    expected_sha256: str | None = None
    reason: str | None = None
    if not isinstance(declared, dict):
        reason = "extract manifest has no replayConfiguration fingerprint"
    elif declared.get("schemaVersion") != 1:
        reason = f"extract manifest has unsupported replayConfiguration schemaVersion {declared.get('schemaVersion')!r}"
    elif declared.get("sha256") in (None, ""):
        reason = "extract manifest replayConfiguration has no sha256"
    else:
        expected_sha256 = str(declared["sha256"])
        if expected_sha256 != current_sha256:
            reason = "transform or target API adapter settings changed after extraction"

    matches = reason is None
    if not matches and not allow_drift:
        raise RuntimeError(
            f"Replay configuration validation failed: {reason}. Start a new extract run, "
            "or set load.allowReplayConfigurationDrift=true for an intentional reprocessing."
        )
    if not matches:
        LOGGER.warning(
            "Replay configuration drift is explicitly allowed for this attempt: %s",
            reason,
        )
    return {
        "schemaVersion": 1,
        "scope": ["transform", "api.target"],
        "expectedSha256": expected_sha256,
        "currentSha256": current_sha256,
        "matches": matches,
        "driftAllowed": allow_drift,
        "driftReason": reason,
    }


def _validate_extract_manifest(
    store: S3Store,
    extract_manifest: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    """Reconcile every staged object against the manifest and return the object inventory.

    Verifies run id, per-registry counts, and each object's key/hash/size/count/version so
    transform/load only ever processes the exact immutable data that extraction recorded.
    """
    if str(extract_manifest.get("runId")) != run_id:
        raise RuntimeError("Extract manifest runId does not match the requested workflow run")
    declared_registries = extract_manifest.get("registries")
    if not isinstance(declared_registries, list):
        raise RuntimeError("Extract manifest registries must be an array")
    if int(extract_manifest.get("registryCount", -1)) != len(declared_registries):
        raise RuntimeError("Extract manifest registryCount does not match its registry entries")

    expected_objects: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    total_records = 0
    required_root = f"runs/run_id={run_id}/raw/"
    for registry in declared_registries:
        if not isinstance(registry, dict) or registry.get("status") != "SUCCEEDED":
            raise RuntimeError("Every registry entry in a successful extract manifest must be successful")
        objects = registry.get("objects")
        if not isinstance(objects, list):
            raise RuntimeError(f"Registry {registry.get('mappingId')} manifest has no object inventory")
        if int(registry.get("objectCount", -1)) != len(objects):
            raise RuntimeError(f"Registry {registry.get('mappingId')} objectCount does not match inventory")
        registry_records = 0
        for expected in objects:
            if not isinstance(expected, dict):
                raise RuntimeError("Extract object inventory entries must be objects")
            key = str(expected.get("key", ""))
            if not key.startswith(required_root):
                raise RuntimeError(f"Extract object key is outside the immutable raw run prefix: {key}")
            if key in seen_keys:
                raise RuntimeError(f"Extract object appears more than once in the manifest: {key}")
            seen_keys.add(key)
            actual = store.inspect_json_lines_object(expected)
            for field_name in ("recordCount", "sha256", "sizeBytes", "versionId"):
                if str(actual.get(field_name)) != str(expected.get(field_name)):
                    raise RuntimeError(
                        f"Staged object reconciliation failed for {key}: {field_name} does not match manifest"
                    )
            registry_records += int(actual["recordCount"])
            expected_objects.append(expected)
        if registry_records != int(registry.get("recordCount", -1)):
            raise RuntimeError(f"Registry {registry.get('mappingId')} staged record count does not match manifest")
        total_records += registry_records
    if total_records != int(extract_manifest.get("recordCount", -1)):
        raise RuntimeError("Staged run record count does not match the extract manifest")
    return expected_objects


def _initialize_summaries(
    extract_manifest: dict[str, Any],
    mapping_by_id: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build the per-mapping summary skeleton (extracted count + zeroed action counters)."""
    summaries: dict[str, dict[str, Any]] = {}
    for extracted in extract_manifest.get("registries", []):
        mapping_id = str(extracted.get("mappingId", ""))
        if mapping_id not in mapping_by_id:
            raise RuntimeError(f"Extract manifest references mapping {mapping_id!r} not present in SSM")
        summaries[mapping_id] = {
            "mappingId": mapping_id,
            "source": public_endpoint(mapping_by_id[mapping_id]["source"]),
            "target": public_endpoint(mapping_by_id[mapping_id]["target"]),
            "extracted": int(extracted.get("recordCount", 0)),
            "transformed": 0,
            "created": 0,
            "updated": 0,
            "existing": 0,
            "dryRun": 0,
            "failed": 0,
            # Compact reasons rendered directly in summary.html. This list is intentionally bounded;
            # the complete failure rows, payloads, and tracebacks are streamed separately.
            "failureDetails": [],
            "warningCount": 0,
            # Source vs target approval state, counted per mapping. The new version creates records in DRAFT, so
            # these two rarely match and the difference is what still needs doing after the load.
            "sourceStatusCounts": {},
            "targetStatusCounts": {},
            "statusesApplied": 0,
            "statusesNotApplied": 0,
            # Per-record status outcomes, seeded so every mapping reports them even when zero.
            # These are counted as records are processed rather than derived from the two status
            # count maps above, which cannot express which record ended up in which status.
            "recordsStrandedInDraft": 0,
            "statusMismatched": 0,
        }
    return summaries


def _verify_mapping_has_not_changed(
    envelope: dict[str, Any],
    current_mapping: dict[str, Any],
) -> None:
    """Fail if a mapping's endpoints changed between extraction and this attempt."""
    for side in ("source", "target"):
        staged = envelope.get(side)
        current = current_mapping.get(side)
        if not isinstance(staged, dict) or not isinstance(current, dict):
            raise ValueError(f"Staged and current {side} endpoints must be objects")
        for field_name in ("accountId", "region", "registryId", "roleArn", "externalId"):
            if staged.get(field_name) != current.get(field_name):
                raise RuntimeError(
                    f"Mapping configuration changed after extraction: {side}.{field_name}. "
                    "Start a new extract run instead of replaying this run."
                )


def _commit_watermarks(
    store: S3Store,
    extract_manifest: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
    *,
    run_id: str,
    attempt_id: str,
    dry_run: bool,
) -> None:
    """Promote each mapping's extract candidate to its saved incremental watermark.

    Skipped entirely for a dry run, and skipped per mapping when that mapping had any record
    failure, so the next incremental run re-reads anything that did not make it to the target registry.
    """
    candidates = {
        str(entry.get("mappingId")): entry.get("candidateWatermark")
        for entry in extract_manifest.get("registries", [])
        if isinstance(entry, dict)
    }
    for mapping_id, summary in summaries.items():
        candidate = candidates.get(mapping_id)
        if dry_run:
            summary["watermarkCommitted"] = False
            summary["watermarkSkipReason"] = "dry run: nothing was written to the target registry"
            continue
        if not isinstance(candidate, dict):
            summary["watermarkCommitted"] = False
            summary["watermarkSkipReason"] = "extract manifest carried no watermark candidate"
            continue
        if int(summary.get("failed", 0)) > 0:
            summary["watermarkCommitted"] = False
            summary["watermarkSkipReason"] = (
                f"{summary['failed']} record(s) failed; the watermark stays put so the next "
                "incremental run re-reads them"
            )
            continue
        loaded = int(summary.get("created", 0)) + int(summary.get("updated", 0)) + int(summary.get("existing", 0))
        committed = watermark_state.commit(
            candidate,
            run_id=run_id,
            attempt_id=attempt_id,
            loaded_at=utc_now(),
            loaded_record_count=loaded,
        )
        key = watermark_state.write(store, mapping_id, committed)
        summary["watermarkCommitted"] = True
        summary["watermark"] = committed
        summary["watermarkArtifact"] = store.location(key)
        LOGGER.info(
            "Committed watermark for mapping %s: maxUpdatedAt=%s (%d records loaded)",
            mapping_id,
            committed.get("maxUpdatedAt"),
            loaded,
        )


def _commit_id_maps(
    store: S3Store,
    summaries: dict[str, dict[str, Any]],
    crosswalk_rows: dict[str, list[dict[str, Any]]],
    *,
    run_id: str,
    dry_run: bool,
) -> None:
    """Fold this run's old->new record ids into each mapping's saved id map.

    Unlike the watermark, this is committed even when records failed, and it records a record that
    was created and then failed to settle. Both follow from what the map is for: the entries name
    records that exist in the target registry, and forgetting one does not make the next run re-read it
    safely -- it makes the next run create a second copy of it.

    Skipped for a dry run, which creates nothing to remember.
    """
    for mapping_id, summary in summaries.items():
        if dry_run:
            summary["idMapCommitted"] = False
            summary["idMapSkipReason"] = "dry run: nothing was written to the target registry"
            continue
        pairs = {
            str(row["oldRecordId"]): str(row["newRecordId"])
            for row in crosswalk_rows.get(mapping_id, [])
            if row.get("oldRecordId") and row.get("newRecordId")
        }
        merged = watermark_state.merge_idmap(watermark_state.read_idmap(store, mapping_id), pairs)
        key = watermark_state.write_idmap(store, mapping_id, merged, run_id=run_id, updated_at=utc_now())
        summary["idMapCommitted"] = True
        summary["idMapArtifact"] = store.location(key)
        summary["idMapRecordCount"] = len(merged)
        LOGGER.info(
            "Committed id map for mapping %s: %d record(s) known, %d from this run",
            mapping_id,
            len(merged),
            len(pairs),
        )


_CROSSWALK_COLUMNS = (
    "oldRecordId",
    "newRecordId",
    # Both names, because they are not always the same string: when two Preview records shared a
    # name, the new version cannot, so the migrated record carries a disambiguated `name`. Anything that looked
    # records up by the Preview name needs this column to find its record.
    "previewName",
    "name",
    "displayName",
    "recordType",
    "recordVersion",
    "action",
    "status",
    # The status the record ended up in, so the crosswalk alone answers "is it live in the target registry yet".
    "targetStatus",
)


# Characters a spreadsheet treats as the start of a formula rather than text. A record name is
# whatever the source registry holds, and the crosswalk exists to be opened in a spreadsheet, so a
# name beginning with one of these would be evaluated on open instead of displayed.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> str:
    """Render one crosswalk cell, neutralising anything a spreadsheet would treat as a formula.

    Prefixing with an apostrophe is the conventional fix: Excel, LibreOffice and Sheets all read it
    as "the rest of this cell is literal text" and do not show it. RFC 4180 quoting -- which
    ``csv.writer`` already does -- protects the *file* structure, not the consumer, so it does not
    help here.
    """
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _crosswalk_csv(rows: list[dict[str, Any]]) -> str:
    """Render crosswalk rows as CSV text (header + one row per record, RFC 4180 quoting)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(_CROSSWALK_COLUMNS),
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({column: _csv_safe(row.get(column)) for column in _CROSSWALK_COLUMNS})
    return buffer.getvalue()


def _write_crosswalks(
    store: S3Store,
    prefix: str,
    summaries: dict[str, dict[str, Any]],
    crosswalk_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    """Write one old->new recordId crosswalk CSV per registry; return each mapping's S3 URI.

    A file is emitted for every mapping (header-only when it had no records) so a reviewer
    always finds a per-registry crosswalk at a predictable location.
    """
    locations: dict[str, str] = {}
    for mapping_id in summaries:
        key = f"{prefix}/mapping={safe_segment(mapping_id)}.csv"
        store.put_text(key, _crosswalk_csv(crosswalk_rows.get(mapping_id, [])), content_type="text/csv")
        locations[mapping_id] = store.location(key)
    return locations


def _resolve_attempt_id(arguments: dict[str, str]) -> str:
    supplied = arguments.get("ATTEMPT_ID") or arguments.get("JOB_RUN_ID")
    return safe_segment(supplied or str(uuid.uuid4()))


def _old_record_id(envelope: dict[str, Any]) -> str | None:
    normalized = envelope.get("oldRecordId")
    if normalized not in (None, ""):
        return str(normalized)
    return None


def run() -> None:
    """Entrypoint wrapper used by the Glue shim: run transform/load, failing the job on error."""
    try:
        main()
    except Exception:
        LOGGER.exception("Transform/load job failed")
        sys.exit(1)


if __name__ == "__main__":
    run()
