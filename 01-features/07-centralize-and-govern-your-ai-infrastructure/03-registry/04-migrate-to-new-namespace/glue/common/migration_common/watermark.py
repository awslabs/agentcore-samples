"""Incremental-load watermarks: remember what was already migrated, per mapping.

An ``INCREMENTAL`` run needs a cutoff. Rather than making an operator supply a timestamp every
time, the engine remembers one per registry mapping and uses it automatically.

The watermark records the **last successful load**, not the last extract. That distinction
matters: if extraction succeeds but the load fails, advancing the watermark would permanently skip
those records. So extraction only proposes a *candidate* (written into the extract manifest), and
transform/load commits it once the records for that mapping are actually in the target registry.

Cutoff selection, in order:

1. An explicit ``changedAfter`` in the run configuration always wins -- it is auditable and lets
   an operator re-migrate a window deliberately.
2. Otherwise the committed watermark's ``maxUpdatedAt``, minus an overlap buffer.
3. If neither exists, the mapping errors instead of silently degrading to a full load.

The overlap buffer re-migrates a few minutes either side of the boundary to absorb clock skew and
records updated *during* the previous run. Re-processing is harmless because the load is an
idempotent upsert.

This module also holds the **id map**, the other thing a run has to remember for the next one:
which target record each source record became, and -- so a later run cannot hand a name to a
different record -- what that target record is called. Both are committed state under ``state/``, keyed per
mapping, read at the start of a run and written at the end, so they live together rather than in
two near-identical modules. Their names are prefixed (``read_idmap`` beside ``read``) because their
commit rules are deliberately *not* the same -- see ``read_idmap`` below.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .util import parse_timestamp, safe_segment

# S3 prefix for committed watermarks. Kept outside runs/ and reports/ so run-data lifecycle
# expiry never deletes the state the next incremental run depends on.
WATERMARK_PREFIX = "state/watermarks"

# S3 prefix for committed id maps. Alongside the watermarks and, like them, outside runs/ and
# reports/ so run-data lifecycle expiry never deletes the state the next load depends on.
IDMAP_PREFIX = "state/idmap"

# Re-scan this many seconds before the recorded boundary. Cheap because the load upserts.
DEFAULT_OVERLAP_SECONDS = 300

SCHEMA_VERSION = 1


class WatermarkError(RuntimeError):
    """Raised when an incremental run cannot determine a cutoff."""


class IdMapError(RuntimeError):
    """Raised when a stored id map cannot be read."""


def watermark_key(mapping_id: str) -> str:
    """Return the S3 key holding the committed watermark for ``mapping_id``."""
    return f"{WATERMARK_PREFIX}/mapping={safe_segment(mapping_id)}.json"


def to_iso(value: dt.datetime) -> str:
    """Format a datetime as an ISO-8601 UTC string with a ``Z`` suffix."""
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def resolve_cutoff(
    *,
    mapping_id: str,
    load_mode: str,
    changed_after: str | None,
    watermark: dict[str, Any] | None,
    overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
) -> tuple[str | None, str]:
    """Return ``(cutoff_iso, reason)`` for a mapping's extract.

    ``cutoff_iso`` is ``None`` for a FULL load. ``reason`` explains the choice and is recorded in
    the manifest and report so a reviewer can see why a run migrated the set it did.
    """
    if str(load_mode).upper() != "INCREMENTAL":
        return None, "FULL load: every source record is extracted"

    if changed_after:
        # Explicit operator intent wins over any stored state.
        return to_iso(parse_timestamp(changed_after)), (
            f"INCREMENTAL from the configured changedAfter ({changed_after})"
        )

    if not watermark:
        raise WatermarkError(
            f"Mapping {mapping_id!r} has no saved watermark, so an INCREMENTAL run has no cutoff. "
            "Run a FULL load once to establish the watermark, or set changedAfter in the run "
            "configuration to migrate a specific window."
        )

    recorded = watermark.get("maxUpdatedAt") or watermark.get("lastLoadedAt")
    if not recorded:
        raise WatermarkError(
            f"Mapping {mapping_id!r} has a watermark without a usable timestamp "
            f"({watermark!r}). Set changedAfter explicitly or run a FULL load."
        )

    boundary = parse_timestamp(recorded) - dt.timedelta(seconds=max(0, int(overlap_seconds)))
    return to_iso(boundary), (
        f"INCREMENTAL from the saved watermark {recorded} minus a {overlap_seconds}s overlap "
        f"(last loaded at {watermark.get('lastLoadedAt', 'unknown')} by run "
        f"{watermark.get('lastRunId', 'unknown')})"
    )


def newest_timestamp(values: list[Any]) -> str | None:
    """Return the newest parsable timestamp in ``values`` as ISO-8601, or ``None``."""
    parsed = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            parsed.append(parse_timestamp(value))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return None
    return to_iso(max(parsed))


def build_candidate(
    *,
    mapping_id: str,
    run_id: str,
    extracted_at: str,
    max_updated_at: str | None,
    record_count: int,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the candidate watermark that extraction proposes for a mapping.

    ``maxUpdatedAt`` is the newest source ``updatedAt`` seen. When a run extracts no records (an
    incremental run with nothing new), the previous boundary is carried forward so the watermark
    never moves backwards. Falling back to the extract time would be wrong: source records can
    carry timestamps older than the run that observed them.
    """
    previous_max = (previous or {}).get("maxUpdatedAt")
    boundary = newest_timestamp([max_updated_at, previous_max])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mappingId": mapping_id,
        "maxUpdatedAt": boundary,
        "extractedAt": extracted_at,
        "extractRunId": run_id,
        "recordCount": int(record_count),
    }


def commit(
    candidate: dict[str, Any],
    *,
    run_id: str,
    attempt_id: str,
    loaded_at: str,
    loaded_record_count: int,
) -> dict[str, Any]:
    """Turn an extract candidate into the committed watermark after a successful load."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mappingId": candidate.get("mappingId"),
        "maxUpdatedAt": candidate.get("maxUpdatedAt"),
        "lastLoadedAt": loaded_at,
        "lastRunId": run_id,
        "lastAttemptId": attempt_id,
        "lastLoadedRecordCount": int(loaded_record_count),
        "extractedAt": candidate.get("extractedAt"),
    }


def read(store: Any, mapping_id: str) -> dict[str, Any] | None:
    """Read the committed watermark for ``mapping_id``, or ``None`` when absent."""
    value = store.get_json_if_present(watermark_key(mapping_id))
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WatermarkError(f"Watermark {watermark_key(mapping_id)} is not a JSON object")
    return value


def write(store: Any, mapping_id: str, value: dict[str, Any]) -> str:
    """Persist the committed watermark for ``mapping_id`` and return its key."""
    key = watermark_key(mapping_id)
    store.put_json(key, value)
    return key


def idmap_key(mapping_id: str) -> str:
    """Return the S3 key holding the committed source-recordId -> target-recordId map."""
    return f"{IDMAP_PREFIX}/mapping={safe_segment(mapping_id)}.json"


def read_idmap(store: Any, mapping_id: str) -> dict[str, str]:
    """Read ``mapping_id``'s committed old->new record ids, empty when there are none yet.

    Every run already writes these pairs into its report as the id crosswalk, for people to repoint
    their own references with. Keeping the same facts as engine state is what lets the *loader* read
    them on the next run: without it, a second run has only two ways to recognise a record it
    already migrated -- the name, and, for URL-synchronized records, the descriptor source. A record
    renamed in Preview between two runs defeats both, so it would be migrated a second time as a new
    target record, leaving the one it was migrated to the first time behind as an orphan. The source
    recordId is the only identifier that survives a rename, so it is the one worth persisting.

    Unlike the watermark, this is committed even when part of a run failed, and even for a record
    that was created and then failed to settle. Both follow from what the map is for: the entries
    name records that already exist in the target registry, and forgetting one does not make the next
    run re-read it safely -- it makes the next run create a second copy of it. Dry runs write no map,
    having created nothing to remember.

    A missing map is normal -- it is the first run for this mapping -- and reads as empty rather than
    an error. A *malformed* one is an error: silently treating it as empty would migrate every
    renamed record a second time.
    """
    value = store.get_json_if_present(idmap_key(mapping_id))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise IdMapError(f"Id map {idmap_key(mapping_id)} is not a JSON object")
    records = value.get("records", {})
    if not isinstance(records, dict):
        raise IdMapError(f"Id map {idmap_key(mapping_id)} has a non-object 'records'")
    return {str(old): str(new) for old, new in records.items() if old not in (None, "") and new not in (None, "")}


def merge_idmap(previous: dict[str, str], pairs: dict[str, str]) -> dict[str, str]:
    """Fold this run's old->new pairs into the stored ones, newer winning.

    Additive on purpose: a mapping absent from ``pairs`` was simply not in this run's window (every
    incremental run carries a fraction of the registry), which is not a reason to forget it.
    """
    merged = dict(previous)
    for old, new in pairs.items():
        if old in (None, "") or new in (None, ""):
            continue
        merged[str(old)] = str(new)
    return merged


def read_idmap_names(store: Any, mapping_id: str) -> dict[str, dict[str, str | None]]:
    """Read the target identity each source record was migrated *under*, empty when unrecorded.

    Stored in the same object as the id map because it answers the same question at a different
    granularity: ``records`` says which target record a source record became, ``names`` says what
    that record is called. Returned as ``{sourceRecordId: {"name": ..., "recordVersion": ...}}``;
    ``recordVersion`` is kept with the name because the target dedup key is the pair, so a name is
    only taken for the version it was taken with.

    The loader needs this to keep ``transform.duplicateNames = "suffix"`` stable. Which record keeps
    a shared name has to be the same answer on every run, and without committed state the only
    answer available is "whichever record this run happened to stage first" -- which silently renames
    records when the next run stages a different subset, or the same set in a different order. See
    ``plan_target_names`` in the transform/load job.

    Individually missing or malformed entries are skipped rather than fatal: a map written before
    this was recorded has none at all, and "not recorded" is a state the loader has to handle anyway.
    A non-object ``names`` is an error, for the same reason a non-object ``records`` is.
    """
    value = store.get_json_if_present(idmap_key(mapping_id))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise IdMapError(f"Id map {idmap_key(mapping_id)} is not a JSON object")
    names = value.get("names", {})
    if not isinstance(names, dict):
        raise IdMapError(f"Id map {idmap_key(mapping_id)} has a non-object 'names'")
    resolved: dict[str, dict[str, str | None]] = {}
    for old, entry in names.items():
        if old in (None, "") or not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name in (None, ""):
            continue
        version = entry.get("recordVersion")
        resolved[str(old)] = {
            "name": str(name),
            "recordVersion": None if version in (None, "") else str(version),
        }
    return resolved


def merge_idmap_names(
    previous: dict[str, dict[str, str | None]],
    entries: dict[str, dict[str, str | None]],
) -> dict[str, dict[str, str | None]]:
    """Fold this run's assigned target identities into the stored ones, newer winning.

    Additive for the same reason ``merge_idmap`` is: a record this run's window did not carry still
    exists in the target registry under the name it was given, and forgetting that name is what lets
    a later run hand it to a different record.
    """
    merged = dict(previous)
    for old, entry in entries.items():
        if old in (None, "") or not isinstance(entry, dict) or entry.get("name") in (None, ""):
            continue
        version = entry.get("recordVersion")
        merged[str(old)] = {
            "name": str(entry["name"]),
            "recordVersion": None if version in (None, "") else str(version),
        }
    return merged


def write_idmap(
    store: Any,
    mapping_id: str,
    records: dict[str, str],
    *,
    run_id: str,
    updated_at: str,
    names: dict[str, dict[str, str | None]] | None = None,
) -> str:
    """Persist ``mapping_id``'s id map and return its key.

    The whole document is rewritten, so ``records`` and ``names`` must both be the merged sets (see
    ``merge_idmap`` and ``merge_idmap_names``), not just what this run produced.
    """
    key = idmap_key(mapping_id)
    store.put_json(
        key,
        {
            "schemaVersion": SCHEMA_VERSION,
            "mappingId": mapping_id,
            "lastRunId": run_id,
            "updatedAt": updated_at,
            "recordCount": len(records),
            "records": records,
            "names": names or {},
        },
    )
    return key
