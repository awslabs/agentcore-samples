"""The one Python entrypoint the ``agent-registry-migration`` CLI drives.

Everything a user does goes through the npm CLI, which shells into this dispatcher::

    python3 -m migration_common check   [config arguments]
    python3 -m migration_common extract [config arguments] --run-id <id>
    python3 -m migration_common load    [config arguments] --run-id <id> [--live true]
    python3 -m migration_common report  [config arguments] [--run-id <id>]
    python3 -m migration_common latest-run         [config arguments]
    python3 -m migration_common target-config      [config arguments] --output-dir <dir> [--create true]
    python3 -m migration_common account
    python3 -m migration_common engine-info        [--stack-name <name>] [--region <r>]
    python3 -m migration_common bucket-info        --bucket <name> [--region <r>]
    python3 -m migration_common glue-run           --job <name> --run-id <id> [--live true]
                                                   [--load-mode INCREMENTAL] [--changed-after <ts>]
    python3 -m migration_common publish-artifacts  --staging-bucket <bucket> --app-dir <dir>
    python3 -m migration_common destroy  [--stack-name <name>] [--region <r>] [--yes] ...

This is deliberately an internal seam, not a second user interface: the CLI owns the flags a person
types and translates them into the calls below, so there is exactly one place to learn. The two
stage entrypoints Glue itself runs (``glue/extract.py`` and ``glue/transform_load.py``) call the
same job modules this does.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from threading import Event
from typing import Any

from .jobs import extract as extract_job
from .jobs import transform_load as transform_load_job
from .settings import ConfigurationError, flag, optional_argument, parse_job_arguments
from .stores import resolve_store
from .util import configure_logging, parse_timestamp

LOGGER = logging.getLogger("agent-registry-migration")

#: How often ``glue-run`` asks Glue for a job run's state.
_GLUE_POLL_INTERVAL_SECONDS = 15
#: Never set. Waiting on it is a bounded, interruptible delay between Glue state polls, stated as
#: "wait for a signal that never comes, at most this long" rather than as an unconditional pause.
#: The interval and the surrounding deadline check are unchanged.
_GLUE_POLL_WAIT = Event()
#: How long ``glue-run`` keeps watching before it stops and says how to check by hand. Comfortably
#: above the 3-hour default job timeout, so this only fires when a run is genuinely stuck rather
#: than merely slow. Watching forever meant a wedged run wedged the command with it.
_GLUE_WATCH_TIMEOUT_SECONDS = 8 * 60 * 60

COMMANDS = (
    "check",
    "extract",
    "load",
    "report",
    "latest-run",
    "target-config",
    "account",
    "engine-info",
    "bucket-info",
    "clear-pending-stack",
    "glue-run",
    "publish-artifacts",
    "destroy",
)


# --------------------------------------------------------------------------------------------
# check -- the pre-flight validation that used to be its own script
# --------------------------------------------------------------------------------------------


def _source_prober(settings: dict[str, Any], purpose: str):
    """Return a callable that proves a Preview registry is listable with its credentials."""
    from .aws_auth import invoker_for_endpoint
    from .registry_api import PreviewRegistryClient

    def probe(endpoint: dict[str, Any]) -> None:
        invoker = invoker_for_endpoint(endpoint, run_id=None, purpose=purpose)
        client = PreviewRegistryClient(invoker, settings["api"]["preview"], str(endpoint["region"]))
        # One page, one record: the cheapest call that proves credentials, permission and existence.
        next(
            iter(
                client.iter_records(
                    registry_id=str(endpoint["registryId"]),
                    load_mode="FULL",
                    changed_after=None,
                )
            ),
            None,
        )

    return probe


def _target_prober(settings: dict[str, Any], purpose: str):
    """Return a callable that proves a target registry is listable with its credentials."""
    from .aws_auth import invoker_for_endpoint
    from .registry_api import TargetRegistryClient

    def probe(endpoint: dict[str, Any]) -> None:
        invoker = invoker_for_endpoint(endpoint, run_id=None, purpose=purpose)
        client = TargetRegistryClient(invoker, settings["api"]["target"], str(endpoint["region"]))
        client.list_records_page(registry_id=str(endpoint["registryId"]))

    return probe


def check(arguments: dict[str, str]) -> int:
    """Validate the configuration and probe every registry and the staging location.

    Reads the same configuration the jobs read and runs the same checks the extract stage enforces,
    so a wrong registry id or a missing permission surfaces in seconds instead of part-way through
    a run. ``--offline`` skips every AWS call, for validating a configuration before access exists.
    """
    import boto3

    from . import preflight
    from . import watermark as watermark_state
    from .settings import resolve_configuration

    offline = flag(arguments, "OFFLINE")
    settings, mappings, config_source = resolve_configuration(arguments)
    # Not required: configuration-only validation is useful before any storage exists. Skipped
    # entirely when offline, because probing a staging bucket is itself an AWS call.
    store = None
    if not offline:
        store, _staging = resolve_store(arguments, settings, required=False, boto3_module=boto3)

    watermark_reader = None
    source_prober = None
    target_prober = None
    if not offline:
        if store is not None:
            watermark_reader = lambda mapping_id: watermark_state.read(store, mapping_id)
        source_prober = _source_prober(settings, "preflight")
        target_prober = _target_prober(settings, "preflight")

    report = preflight.run_checks(
        settings,
        mappings,
        store=store,
        watermark_reader=watermark_reader,
        source_prober=source_prober,
        target_prober=target_prober,
        # This entrypoint is only ever reached from the CLI on someone's machine, so the checks
        # about that machine's own AWS configuration apply here and nowhere else.
        workstation=True,
    )
    if flag(arguments, "JSON"):
        print(json.dumps({**report.as_dict(), "configurationSource": config_source}, indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 1


# --------------------------------------------------------------------------------------------
# report -- read a run's reports back without hunting for keys
# --------------------------------------------------------------------------------------------


def report(arguments: dict[str, str]) -> int:
    """Print what a run did: what extraction read, and what the load attempt wrote.

    With no ``--run-id`` it reports the most recent run in the staging location, so reviewing a run
    never requires copying an id out of a log.
    """
    import boto3

    from .settings import resolve_configuration

    settings, _mappings, _source = resolve_configuration(arguments)
    store, staging = resolve_store(arguments, settings, boto3_module=boto3)

    run_id = optional_argument(arguments, "RUN_ID")
    if not run_id:
        run_id = _latest_run_id(store)
        if not run_id:
            print(f"No migration runs found in {staging}.", file=sys.stderr)
            return 1

    extract_summary = store.get_json_if_present(f"reports/run_id={run_id}/extract-summary.json")
    attempts = _attempt_summaries(store, run_id)
    if extract_summary is None and not attempts:
        print(f"No reports for run {run_id} in {staging}.", file=sys.stderr)
        return 1

    if flag(arguments, "JSON"):
        print(
            json.dumps(
                {"runId": run_id, "extract": extract_summary, "attempts": attempts},
                indent=2,
            )
        )
        return 0

    _ensure_report_page(store, run_id, extract_summary, attempts)
    print(_render_report(run_id, store, extract_summary, attempts))
    latest = attempts[-1] if attempts else None
    return 0 if latest is None or latest.get("status") != "FAILED" else 1


def _ensure_report_page(
    store: Any,
    run_id: str,
    extract_summary: Any,
    attempts: list[dict[str, Any]],
) -> None:
    """Write the HTML report for any extraction or attempt that has none.

    Both stages write their own page, so this only fires for a run extracted or loaded before the
    page existed -- and for those, regenerating from the JSON report costs one write and means the
    reviewable page is never missing just because of when the run happened.
    """
    from . import report_html

    # One listing of the run's report prefix, reused for every existence check below. Each check
    # used to list a prefix of its own -- and for the extract page that prefix is the whole run,
    # which on a large migration is every record-comparison part file, fetched to answer whether one
    # key exists.
    existing = set(store.list_keys(f"reports/run_id={run_id}/"))

    if isinstance(extract_summary, dict):
        extract_key = f"reports/run_id={run_id}/extraction.html"
        if extract_key not in existing:
            store.put_text(
                extract_key,
                report_html.render_extract_report(extract_summary),
                content_type="text/html",
            )

    for attempt in attempts:
        attempt_id = str(attempt.get("attemptId", ""))
        if not attempt_id:
            continue
        key = f"reports/run_id={run_id}/attempt={attempt_id}/summary.html"
        if key in existing:
            continue
        store.put_text(
            key,
            report_html.render_report(
                attempt,
                extract_summary if isinstance(extract_summary, dict) else None,
            ),
            content_type="text/html",
        )


def _latest_run_id(store: Any) -> str | None:
    """Return the most recent run in the staging location, by when it actually started."""
    run_ids = _run_ids(store)
    return run_ids[-1] if run_ids else None


def _run_ids(store: Any, summaries: dict[str, Any] | None = None) -> list[str]:
    """Every run id in the staging location, oldest first, ordered by when each run started.

    The ordering deliberately does not trust the run id. Generated ids are timestamp-prefixed
    (``20260730T115003Z-2167cf8f``) and do sort chronologically, but ``--run-id`` accepts any
    string, so a single hand-supplied id sorts wherever its characters fall: ``wr-shapes-...``
    lands after every generated id, and a plain string sort then answers "which run is newest"
    with the oldest one in the bucket. Each run's own recorded ``startedAt`` is the only ordering
    that survives a caller-chosen id, so that is what is used, with the id as the tie-breaker.

    ``summaries`` is a cache the caller can pass so the extract report of each run is fetched once
    rather than once per caller. Ordering costs one read per run, and ``latest_run`` used to read
    every one of them again immediately afterwards to find the newest loadable extract -- twice the
    requests for the same answer, over a bucket that accumulates a run per migration attempt.
    """
    cache = summaries if summaries is not None else {}
    run_ids = set()
    for key in store.list_keys("reports/run_id="):
        segment = key.split("/", 2)[1] if key.startswith("reports/") else ""
        if segment.startswith("run_id="):
            run_ids.add(segment[len("run_id=") :])
    return sorted(run_ids, key=lambda run_id: (_run_started_at(store, run_id, cache), run_id))


def _extract_summary(store: Any, run_id: str, cache: dict[str, Any]) -> Any:
    """Read a run's extract report, remembering it so repeated questions cost one request."""
    if run_id not in cache:
        cache[run_id] = store.get_json_if_present(f"reports/run_id={run_id}/extract-summary.json")
    return cache[run_id]


def _run_started_at(store: Any, run_id: str, cache: dict[str, Any] | None = None) -> str:
    """When ``run_id`` started, normalized for comparison, or ``""`` when it cannot be read.

    Read from the run's own extract report rather than inferred from the id. A run whose start
    time is missing or unparseable sorts oldest: being unable to date a run is not a reason to
    hide it, but it is a reason not to offer it as the newest.
    """
    summary = _extract_summary(store, run_id, cache if cache is not None else {})
    started_at = summary.get("startedAt") if isinstance(summary, dict) else None
    if started_at in (None, ""):
        return ""
    try:
        return parse_timestamp(started_at).isoformat()
    except (TypeError, ValueError):
        return ""


def latest_run(arguments: dict[str, str]) -> int:
    """Print the newest run whose extraction is ready to load.

    So loading an extract you have already reviewed does not mean copying a run id out of a log:
    the CLI asks for this and passes it back as ``--run-id``. A run whose extraction failed is
    skipped rather than offered, because loading it would load an incomplete registry.
    """
    import boto3

    from .settings import resolve_configuration

    settings, _mappings, _source = resolve_configuration(arguments)
    store, staging = resolve_store(arguments, settings, boto3_module=boto3)

    # Shared with the ordering pass, which has already read every one of these.
    summaries: dict[str, Any] = {}
    for run_id in reversed(_run_ids(store, summaries)):
        summary = _extract_summary(store, run_id, summaries)
        if isinstance(summary, dict) and summary.get("readyForTransform"):
            print(run_id)
            return 0
    print(
        f"No extract that is ready to load was found in {staging}. Run a dry run first: agent-registry-migration run",
        file=sys.stderr,
    )
    return 1


def _attempt_summaries(store: Any, run_id: str) -> list[dict[str, Any]]:
    """Load every load-attempt summary for ``run_id``, oldest first."""
    prefix = f"reports/run_id={run_id}/"
    summaries = []
    for key in store.list_keys(prefix):
        if key.endswith("/summary.json") and "/attempt=" in key:
            value = store.get_json_if_present(key)
            if isinstance(value, dict):
                summaries.append(value)
    return sorted(summaries, key=lambda item: str(item.get("startedAt", "")))


def _render_report(
    run_id: str,
    store: Any,
    extract_summary: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
) -> str:
    """Render a run as a short, scannable summary with the numbers that decide the next step."""
    lines = [f"Run {run_id}", ""]
    if extract_summary:
        totals = extract_summary.get("totals", {})
        lines.append(
            f"Extract: {extract_summary.get('status')} -- {totals.get('records', 0)} record(s) "
            f"from {totals.get('registries', 0)} registry mapping(s)"
        )
        for registry in extract_summary.get("registries", []):
            types = registry.get("recordTypeCounts", {})
            rendered = ", ".join(f"{name}={count}" for name, count in sorted(types.items()))
            lines.append(
                f"  {registry.get('mappingId')}: {registry.get('recordCount', 0)} record(s)"
                + (f" ({rendered})" if rendered else "")
            )
        if not extract_summary.get("readyForTransform", False):
            lines.append("  NOT ready to load: fix the errors above and extract again")
        lines.append("")

    if not attempts:
        lines.append("Load: not run yet for this extract")
        extract_html = next(
            (loc for loc, _ in (extract_summary or {}).get("artifacts", {}).items() if "extraction.html" in loc),
            store.location(f"reports/run_id={run_id}/extraction.html"),
        )
        lines += ["", f"Report: {extract_html}"]
        return "\n".join(lines)

    latest = attempts[-1]
    mode = "DRY RUN (nothing written to the target registry)" if latest.get("dryRun") else "LIVE"
    lines.append(
        f"Load: {latest.get('status')} -- {mode}"
        + (f", attempt {len(attempts)} of {len(attempts)}" if len(attempts) > 1 else "")
    )
    for registry in latest.get("registries", []):
        lines.append(
            f"  {registry.get('mappingId')}: created={registry.get('created', 0)} "
            f"updated={registry.get('updated', 0)} unchanged={registry.get('existing', 0)} "
            f"dryRun={registry.get('dryRun', 0)} failed={registry.get('failed', 0)}"
        )
    approval = latest.get("approval") or {}
    if approval.get("recordsNeedingResubmission"):
        lines.append(
            f"  {approval['recordsNeedingResubmission']} record(s) were past DRAFT in Preview and "
            "are DRAFT in the target registry -- submit them for approval when ready"
        )
    # A record whose content loaded but whose status could not be reproduced is a successful record
    # with unfinished business, so it never reaches errorCount. Printing it is the difference
    # between a run that looks clean and a run that is: the record exists in the target registry but sits in the
    # wrong status, which for anything past DRAFT means the data plane cannot see it.
    if approval.get("statusesNotApplied"):
        lines.append(
            f"  {approval['statusesNotApplied']} record(s) loaded but kept the wrong status "
            "-- see statusError per record in record-comparison/"
        )
    if latest.get("errorCount"):
        lines.append(f"  {latest['errorCount']} record(s) failed")

    html_path = next(
        (loc for loc, _ in (latest.get("artifacts") or {}).items() if "summary.html" in loc),
        store.location(f"reports/run_id={run_id}/summary.html"),
    )
    lines += ["", f"Report: {html_path}"]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# target-config -- derive the target registry configuration to create
# --------------------------------------------------------------------------------------------


def target_config(arguments: dict[str, str]) -> int:
    """Write the target registry ``CreateRegistry`` input derived from each source registry.

    Derives by default and prints the one command that applies the result, because the payload
    decides who may read the registry and is worth a look before it exists. ``--create`` applies it
    here instead: create, wait for ``READY``, and report the generated registry id.
    """
    from . import target_registry
    from .settings import resolve_configuration

    settings, mappings, config_source = resolve_configuration(arguments)
    requested = _split_list(optional_argument(arguments, "MAPPING"))
    unknown = target_registry.unknown_mapping_ids(mappings, requested)
    if unknown:
        known = ", ".join(str(mapping.get("id")) for mapping in mappings) or "(none)"
        print(
            f"error: no mapping {', '.join(unknown)} in {config_source}. Known: {known}",
            file=sys.stderr,
        )
        return 1

    output_dir = optional_argument(arguments, "OUTPUT_DIR")
    entries = target_registry.derive_create_registry_inputs(settings, mappings, mapping_ids=requested)
    create = flag(arguments, "CREATE")
    if create:
        # Before the loop below, so every entry already carries its registry id (or its
        # createError) by the time that entry is reported and by the time --json is emitted.
        target_registry.create_target_registries(settings, mappings, entries)
    failures = 0
    rendered_entries = []
    for entry in entries:
        if entry.get("error"):
            print(f"error: {entry['mappingId']}: {entry['error']}", file=sys.stderr)
            failures += 1
            continue
        body = json.dumps(entry["payload"], indent=2, sort_keys=True) + "\n"
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, f"{entry['mappingId']}.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            entry["payloadPath"] = path
            # Kept even when --create applied the payload: it records exactly what was sent, which
            # is what someone reviewing or reproducing the registry afterwards needs. The command
            # is only offered when nothing was created, so there is one instruction on screen.
            if not create:
                entry["command"] = target_registry.create_registry_command(entry, path)
        else:
            print(f"# {entry['mappingId']}")
            print(body, end="")
        if create:
            if entry.get("createError"):
                print(f"error: {entry['mappingId']}: {entry['createError']}", file=sys.stderr)
                failures += 1
            elif not flag(arguments, "JSON"):
                status = entry.get("status") or "unknown"
                print(f"Created target registry {entry.get('registryId')} for {entry['mappingId']} ({status})")
        # On stderr so it is seen even when stdout is the payload being redirected to a file, and
        # because these need answering before the payload is applied: a dropped authorizer field or
        # an audience still naming the Preview registry is an access-control decision, and this
        # payload is applied by hand.
        for warning in entry.get("warnings") or []:
            print(f"warning: {entry['mappingId']}: {warning}", file=sys.stderr)
        rendered_entries.append(entry)

    # Once, not per mapping, and only when a command was actually emitted: the command cannot run
    # until the CLI has the target service model, and it is emitted to be copied.
    #
    # Skipped under --json, which means something is reading this output rather than a person: the
    # `init` wizard is the caller that does, and it prints the same note itself, in position, right
    # before the commands. Printing here as well put it on screen twice.
    if not flag(arguments, "JSON") and any(entry.get("command") for entry in rendered_entries):
        print("\n" + target_registry.create_registry_prerequisite(), file=sys.stderr)

    if output_dir and flag(arguments, "JSON"):
        print(json.dumps(rendered_entries, indent=2, sort_keys=True))
    return 1 if failures else 0


# --------------------------------------------------------------------------------------------
# publish-artifacts -- upload the Glue job scripts and library wheel
# --------------------------------------------------------------------------------------------


def account(_arguments: dict[str, str]) -> int:
    """Print the calling identity and default region, so the CLI can offer them as defaults.

    Asking someone to type an account id they can be wrong about is a worse experience than
    reading it from the credentials they already have.
    """
    import boto3

    identity = boto3.client("sts").get_caller_identity()
    print(
        json.dumps(
            {
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
                "region": boto3.session.Session().region_name,
            }
        )
    )
    return 0


def bucket_info(arguments: dict[str, str]) -> int:
    """Print whether a bucket name is already taken, and by whom, as JSON.

    `deploy` calls this before creating the staging bucket. S3 names are global and a bucket is
    never adopted by name -- if one already exists, CloudFormation does not reuse it, it fails the
    whole stack create with BucketAlreadyOwnedByYou (or BucketAlreadyExists, if it belongs to a
    different account entirely). Checking first turns that mid-deploy rollback into a plain
    message naming exactly what exists and what to do about it.
    """
    import boto3
    from botocore.exceptions import ClientError

    bucket = optional_argument(arguments, "BUCKET")
    if not bucket:
        print("error: --bucket is required", file=sys.stderr)
        return 1
    region = optional_argument(arguments, "REGION")
    session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchBucket") or status == 404:
            print(json.dumps({"exists": False}))
            return 0
        if status == 403:
            # Owned by a different account: still "exists" for naming purposes, but this
            # credential cannot see into it, so there is nothing further to report.
            print(json.dumps({"exists": True, "accessible": False, "ownedByCaller": False}))
            return 0
        print(f"error: could not check s3://{bucket}: {error}", file=sys.stderr)
        return 1
    # Reachable with these credentials: same account (or an account this role can read into).
    # Tag it against this tool's own stamp so a real collision (an unrelated bucket that happens
    # to match the name) is not mistaken for one of this tool's earlier deployments.
    application_tag = None
    try:
        tagging = s3.get_bucket_tagging(Bucket=bucket)
        application_tag = next(
            (tag.get("Value") for tag in tagging.get("TagSet", []) if tag.get("Key") == "Application"),
            None,
        )
    except ClientError:
        pass
    print(
        json.dumps(
            {
                "exists": True,
                "accessible": True,
                "ownedByCaller": True,
                "applicationTag": application_tag,
            }
        )
    )
    return 0


def clear_pending_stack(arguments: dict[str, str]) -> int:
    """Delete a stack that is stuck in ``REVIEW_IN_PROGRESS``, so a deploy can proceed.

    ``REVIEW_IN_PROGRESS`` is the shell CloudFormation creates when a changeset is created for a
    stack that does not exist yet and then never executed. It holds no resources. A deploy that
    cannot get confirmation -- ``cdk deploy`` with no TTY to prompt at -- leaves exactly that, and
    because the stack carries ``EnableTerminationProtection`` from the very first changeset, the
    *next* deploy cannot clean it up either: CloudFormation answers "Stack [...] cannot be deleted
    while TerminationProtection is enabled" and every later deploy fails the same way.

    So this does what ``destroy`` already does for a real stack -- disable termination protection,
    then delete -- but only ever for this one status, and only after confirming the stack really is
    empty. Anything else is left untouched and reported, because a stack with resources in it is
    not something a deploy should be deleting on anyone's behalf.
    """
    import boto3
    from botocore.exceptions import ClientError

    stack_name = optional_argument(arguments, "STACK_NAME") or "AgentRegistryMigrationEngine"
    region = optional_argument(arguments, "REGION")
    session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
    cloudformation = session.client("cloudformation")

    try:
        stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as error:
        if "does not exist" in str(error):
            print(json.dumps({"cleared": False, "reason": "no such stack"}))
            return 0
        print(f"error: could not describe {stack_name}: {error}", file=sys.stderr)
        return 1

    status = str(stack.get("StackStatus"))
    if status != "REVIEW_IN_PROGRESS":
        print(json.dumps({"cleared": False, "reason": f"status is {status}"}))
        return 0

    # A REVIEW_IN_PROGRESS stack should hold nothing. Verified rather than assumed: this function
    # deletes, so it must not act on a status it has misread.
    resources = cloudformation.list_stack_resources(StackName=stack_name).get("StackResourceSummaries", [])
    if resources:
        print(
            json.dumps(
                {
                    "cleared": False,
                    "reason": f"{len(resources)} resource(s) present despite {status}",
                }
            )
        )
        return 0

    try:
        if stack.get("EnableTerminationProtection"):
            cloudformation.update_termination_protection(
                StackName=stack_name,
                EnableTerminationProtection=False,
            )
        cloudformation.delete_stack(StackName=stack_name)
        cloudformation.get_waiter("stack_delete_complete").wait(StackName=stack_name)
    except ClientError as error:
        print(f"error: could not remove the pending {stack_name}: {error}", file=sys.stderr)
        return 1

    print(json.dumps({"cleared": True, "reason": f"empty stack in {status}"}))
    return 0


def engine_info(arguments: dict[str, str]) -> int:
    """Print a deployed engine's CloudFormation outputs as JSON.

    The CLI uses this to learn the staging bucket and Glue job names after a deploy, so those
    never have to be copied out of the console and into a command.
    """
    import boto3

    stack_name = optional_argument(arguments, "STACK_NAME") or "AgentRegistryMigrationEngine"
    region = optional_argument(arguments, "REGION")
    session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
    try:
        described = session.client("cloudformation").describe_stacks(StackName=stack_name)
    except Exception as error:  # noqa: BLE001 - reported as a message, not a traceback
        print(f"error: could not describe {stack_name}: {error}", file=sys.stderr)
        return 1
    stack = described["Stacks"][0]
    created_at = stack.get("CreationTime")
    updated_at = stack.get("LastUpdatedTime")
    print(
        json.dumps(
            {
                "stackName": stack.get("StackName"),
                "status": stack.get("StackStatus"),
                # When this stack was first created and last changed, straight from CloudFormation.
                # `deploy` reads these before it runs to say whether it is creating a new engine or
                # joining one someone else already deployed into this account/region.
                "creationTime": created_at.isoformat() if created_at else None,
                "lastUpdatedTime": updated_at.isoformat() if updated_at else None,
                "outputs": {
                    str(item.get("OutputKey")): str(item.get("OutputValue")) for item in stack.get("Outputs", [])
                },
            },
            indent=2,
        )
    )
    return 0


def glue_run(arguments: dict[str, str]) -> int:
    """Start a Glue job for one stage and wait for it, reporting the outcome.

    This replaces starting a job and then polling it by hand: the CLI passes the run id it already
    knows, so a Glue run needs no more typing than a local one.
    """
    import time

    import boto3

    job_name = optional_argument(arguments, "JOB")
    run_id = optional_argument(arguments, "RUN_ID")
    if not job_name or not run_id:
        raise ConfigurationError("glue-run needs --job and --run-id")

    region = optional_argument(arguments, "REGION")
    session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
    client = session.client("glue")

    job_arguments = {"--RUN_ID": run_id}
    live = optional_argument(arguments, "LIVE")
    if live is not None:
        job_arguments["--LIVE"] = str(live)
    # Per-run decisions have to reach the job, not just the local stages: a job that is not told the
    # scope falls back to the deployed loadMode, which silently turns an incremental run into a full
    # re-read of every record. The job reads these the same way it reads --RUN_ID.
    for name, job_key in (("LOAD_MODE", "--LOAD_MODE"), ("CHANGED_AFTER", "--CHANGED_AFTER")):
        value = optional_argument(arguments, name)
        if value is not None:
            job_arguments[job_key] = str(value)

    started = client.start_job_run(JobName=job_name, Arguments=job_arguments)
    job_run_id = started["JobRunId"]
    LOGGER.info("Started Glue job %s (run %s)", job_name, job_run_id)

    # A Glue job has its own timeout (engine.glueTimeoutMinutes, 3 hours by default), so this only
    # needs to outlast it and then stop waiting rather than block forever if the state never becomes
    # terminal. The job itself is unaffected: giving up here stops watching, not the run.
    deadline = time.monotonic() + _GLUE_WATCH_TIMEOUT_SECONDS
    terminal = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}
    state = "STARTING"
    while state not in terminal:
        if time.monotonic() > deadline:
            print(
                f"error: stopped waiting for Glue job {job_name} run {job_run_id} after "
                f"{_GLUE_WATCH_TIMEOUT_SECONDS // 3600}h; it is still {state}. The job is still "
                f"running -- check it with: aws glue get-job-run --job-name {job_name} "
                f"--run-id {job_run_id}",
                file=sys.stderr,
            )
            return 1
        _GLUE_POLL_WAIT.wait(_GLUE_POLL_INTERVAL_SECONDS)
        detail = client.get_job_run(JobName=job_name, RunId=job_run_id)["JobRun"]
        new_state = str(detail.get("JobRunState"))
        if new_state != state:
            LOGGER.info("Glue job %s: %s", job_name, new_state)
        state = new_state
    if state != "SUCCEEDED":
        detail = client.get_job_run(JobName=job_name, RunId=job_run_id)["JobRun"]
        print(
            f"error: Glue job {job_name} finished {state}: "
            f"{detail.get('ErrorMessage', 'see the job run in CloudWatch Logs')}",
            file=sys.stderr,
        )
        return 1
    return 0


def publish_artifacts(arguments: dict[str, str]) -> int:
    """Upload the two Glue entrypoints and the ``migration_common`` wheel to ``app/``.

    The stack normally uploads these itself. It cannot when ``engine.createIamRoles`` is false,
    because the CDK construct that would do it provisions a Lambda and therefore an IAM role, so
    the upload happens here with the operator's own credentials instead.
    """
    import boto3

    bucket = optional_argument(arguments, "STAGING_BUCKET")
    app_dir = optional_argument(arguments, "APP_DIR")
    wheel_dir = optional_argument(arguments, "WHEEL_DIR")
    if not bucket or not app_dir:
        raise ConfigurationError("publish-artifacts needs --staging-bucket and --app-dir")

    # Build the client for the region the stack was deployed into, not whatever the ambient session
    # points at: the caller's default region is frequently not the engine's, and uploading to the
    # wrong place would leave the jobs without their entrypoints.
    region = optional_argument(arguments, "REGION")
    session = boto3.session.Session(region_name=region) if region else boto3.session.Session()
    client = session.client("s3")
    published: list[str] = []
    for name in ("extract.py", "transform_load.py"):
        path = os.path.join(app_dir, name)
        if not os.path.isfile(path):
            raise ConfigurationError(f"Glue entrypoint not found: {path}")
        client.upload_file(path, bucket, f"app/{name}")
        published.append(f"s3://{bucket}/app/{name}")
    if wheel_dir:
        # The wheel must keep its PEP 427 filename: Glue Python shell pip-installs it from
        # --extra-py-files and rejects a renamed wheel.
        wheels = sorted(name for name in os.listdir(wheel_dir) if name.endswith(".whl"))
        if not wheels:
            raise ConfigurationError(f"No wheel found in {wheel_dir}")
        for name in wheels:
            client.upload_file(os.path.join(wheel_dir, name), bucket, f"app/{name}")
            published.append(f"s3://{bucket}/app/{name}")
    for location in published:
        print(f"published {location}")
    return 0


# --------------------------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------------------------


def _split_list(value: str | None) -> list[str]:
    return [item.strip() for item in str(value).split(",") if item.strip()] if value else []


def main(argv: list[str] | None = None) -> int:
    values = list(argv if argv is not None else sys.argv[1:])
    if not values or values[0] in {"-h", "--help"}:
        print(__doc__)
        return 0 if values else 2
    command = values[0]
    if command not in COMMANDS:
        print(
            f"error: unknown command {command!r}. Expected one of: {', '.join(COMMANDS)}",
            file=sys.stderr,
        )
        return 2

    arguments = parse_job_arguments(values[1:])
    configure_logging()
    return _dispatch(command, arguments, values[1:])


def _destroy(_arguments: dict[str, str], raw: list[str]) -> int:
    # teardown parses its own argv and skips element 0, the way a program name is skipped, so the
    # command name is passed back in that slot.
    from . import teardown

    return teardown.main(["destroy", *raw])


def _extract(_arguments: dict[str, str], raw: list[str]) -> int:
    extract_job.main(raw)
    return 0


def _load(_arguments: dict[str, str], raw: list[str]) -> int:
    transform_load_job.main(raw)
    return 0


#: command name -> handler. A table rather than an ``if`` chain, and every name in :data:`COMMANDS`
#: must appear here: the chain this replaces had no terminal ``else``, so its fall-through ran the
#: *load* stage -- the one stage that writes to a customer's target registry. A name added to
#: ``COMMANDS`` and forgotten in the chain would have silently started a load.
#: ``test_engine_entrypoint`` asserts the two agree in both directions.
_HANDLERS: dict[str, Any] = {
    "check": lambda arguments, _raw: check(arguments),
    "report": lambda arguments, _raw: report(arguments),
    "latest-run": lambda arguments, _raw: latest_run(arguments),
    "target-config": lambda arguments, _raw: target_config(arguments),
    "account": lambda arguments, _raw: account(arguments),
    "engine-info": lambda arguments, _raw: engine_info(arguments),
    "bucket-info": lambda arguments, _raw: bucket_info(arguments),
    "clear-pending-stack": lambda arguments, _raw: clear_pending_stack(arguments),
    "glue-run": lambda arguments, _raw: glue_run(arguments),
    "publish-artifacts": lambda arguments, _raw: publish_artifacts(arguments),
    "destroy": _destroy,
    "extract": _extract,
    "load": _load,
}


def _dispatch(command: str, arguments: dict[str, str], raw: list[str]) -> int:
    """Run ``command``. Raises rather than guessing when a command has no handler."""
    handler = _HANDLERS.get(command)
    if handler is None:
        # Only reachable if COMMANDS and _HANDLERS disagree, which is a programming error rather
        # than a user one -- so it says so, instead of falling through to whatever came last.
        raise ConfigurationError(
            f"Command {command!r} is advertised but has no handler. This is a bug in "
            "migration_common.__main__; the command table and the handler table have drifted."
        )
    return handler(arguments, raw)


def _debug_requested(argv: list[str] | None = None) -> bool:
    """Whether the caller asked for tracebacks (``--debug``, or ``MIGRATION_DEBUG`` in the env)."""
    values = list(argv if argv is not None else sys.argv[1:])
    if any(item.split("=", 1)[0] in {"--debug", "--DEBUG"} for item in values):
        return True
    return str(os.environ.get("MIGRATION_DEBUG", "")).strip().lower() in {"1", "true", "yes", "on"}


def run() -> int:
    """Entrypoint wrapper: report a configuration problem as a message, not a traceback wall.

    A configuration problem is a message because the message is the whole answer. Anything else is
    a message *plus* a traceback whenever ``--debug`` or ``MIGRATION_DEBUG`` is set: the two stage
    entrypoints log their own detail, but the read-only commands (``report``, ``check``,
    ``engine-info``, ``bucket-info``, ``target-config``, ``publish-artifacts``) do not, so an
    unexpected failure in one of those used to be a single line with no way to get behind it.
    """
    debug = _debug_requested()
    try:
        return main()
    except ConfigurationError as error:
        LOGGER.error("%s", error)
        return 1
    except Exception as error:
        if debug:
            LOGGER.exception("Command failed")
        else:
            LOGGER.error("%s (re-run with --debug, or MIGRATION_DEBUG=1, for the traceback)", error)
        return 1


if __name__ == "__main__":
    sys.exit(run())
