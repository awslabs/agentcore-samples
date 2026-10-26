"""Preview and target registry control-plane clients built on modeled boto3 operations.

The clients talk to the control plane through modeled ``boto3`` (SDK) calls rather than
hand-rolled SigV4 REST. Both models come from the installed SDK: ``bedrock-agentcore-control``
for the Preview reader and ``agent-registry-control`` for the target writer. The HTTP method/URI,
signing name, and shapes therefore come from the service model, so a target wire change is a model
swap, not a code change -- and an SDK without the target service model fails at client construction.

The request/response *field paths* the jobs read (item lists, ids, tokens) still come from
the baked API adapter; because those paths equal the model member names, the same
higher-level list/get/upsert logic works unchanged against the SDK-parsed responses.

* :class:`PreviewRegistryClient` reads (list + optional get) and paginates the source.
* :class:`TargetRegistryClient` performs an idempotent ``upsert``: match an existing record by
  name(+version) or by descriptor-source identity, then create or update and poll to a
  terminal state. The module-level helpers build and validate the target create/update bodies
  and compare a live record against the desired one.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError

from .aws_auth import AwsApiInvoker
from .registry_client import build_control_plane_client
from .util import get_path, parse_timestamp, set_path

LOGGER = logging.getLogger("agent-registry-migration.registry-api")

# Never set. Waiting on it is a bounded, interruptible delay between attempts of an
# already-attempt-limited retry or status-poll loop -- the pause is stated as "wait for a signal
# that never comes, at most this long" rather than as an unconditional pause. Shared and safe to
# wait on from several loader threads at once, because nothing ever sets it.
_POLL_WAIT = threading.Event()

# route name -> boto3 client method. The clients drive modeled SDK operations; the wire
# contract (HTTP method/URI/signing) lives in the service model, not in this code.
_PREVIEW_OPERATIONS = {"list": "list_registry_records", "get": "get_registry_record"}
_TARGET_OPERATIONS = {
    "list": "list_registry_records",
    "create": "create_registry_record",
    "get": "get_registry_record",
    "update": "update_registry_record",
    "submitForApproval": "submit_registry_record_for_approval",
    "updateStatus": "update_registry_record_status",
}

# Statuses a migration can put a record into. Everything else a Preview record might be sitting in
# is either transient (CREATING, UPDATING) or a failure of an operation that never happened here
# (CREATE_FAILED, UPDATE_FAILED), so it describes the source record's history rather than a state a
# freshly created target record can hold.
REPRODUCIBLE_STATUSES = frozenset({"DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"})

# Retry budget for a status transition the service refuses with ConflictException ("Concurrent
# update detected. Please retry."). Deliberately short: the conflict clears in the time it takes
# the previous write on that record to settle, so a few doubling waits either work or mean
# something other than a race is wrong. Overridable through the target poll settings, which is only
# needed by tests that want no waiting at all.
DEFAULT_CONFLICT_RETRY_ATTEMPTS = 5
DEFAULT_CONFLICT_RETRY_DELAY_SECONDS = 0.5

# Poll budget for a record to reach a settled state after a create or update.
DEFAULT_POLL_ATTEMPTS = 90
DEFAULT_POLL_INTERVAL_SECONDS = 2.0

# Poll budget for a *status transition* to become observable, which is a different question from
# the one above and deliberately gets a smaller budget: the record already exists, and submit /
# updateStatus only moves a flag, so it settles in seconds. Spending the record-level budget (90
# attempts x 2s = 3 minutes) on every transition would multiply a large run's duration for nothing.
#
# It is a named, overridable setting (``target.poll.statusMaxAttempts``) rather than the literal 15 it
# used to be: that literal was applied as ``min(maxAttempts, 15)``, so an operator who raised
# ``maxAttempts`` for a slowly-settling registry got no change here and no indication why. The
# record-level ``maxAttempts`` still acts as the ceiling, so a configuration that shortens overall
# polling continues to shorten status polling with it.
DEFAULT_STATUS_POLL_ATTEMPTS = 15


class RegistryApiError(RuntimeError):
    """Raised for any Preview/target control-plane request or contract violation.

    ``record_id`` carries the target recordId when the failure happened *after* the record was
    created -- a create that returns an id and then settles into CREATE_FAILED, or never settles
    at all. The record exists in the target registry either way, so the id has to travel with the
    error: without it the reports describe a failure with no new id, and the record nobody can
    name is an orphan the crosswalk cannot lead a reader to.
    """

    def __init__(
        self,
        message: str,
        *,
        record_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.record_id = record_id
        # The service's own error code when this wraps a ClientError, e.g. ResourceNotFoundException.
        # Callers that need to distinguish "absent" from "broken" read this rather than the message.
        self.error_code = error_code


def _client_error_code(error: Exception) -> str | None:
    """Return the service error code for a ``ClientError``, or None for any other failure."""
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code")
        return str(code) if code else None
    return None


def _preview_endpoint_url(config: dict[str, Any], region: str) -> str:
    """Resolve and pin the Preview endpoint to the fixed regional bedrock-agentcore host."""
    if _optional_string(config.get("endpointUrl")) is not None:
        raise RegistryApiError("Preview endpointUrl overrides are not supported; the regional endpoint is fixed")
    template = _required_string(config, "endpointUrlTemplate", "preview API")
    endpoint = template.replace("{region}", region)
    parsed = urlparse(endpoint)
    expected_host = f"bedrock-agentcore-control.{region}.amazonaws.com"
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != expected_host
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RegistryApiError(
            f"Preview endpointUrlTemplate must resolve to https://{expected_host} for region {region}"
        )
    return endpoint.rstrip("/")


@dataclass(frozen=True)
class ExtractedPreviewRecord:
    """A Preview record plus its normalized source record id."""

    record: dict[str, Any]
    old_record_id: str


class PreviewRegistryClient:
    """Model-independent preview control-plane client using the baked REST/JSON contract."""

    def __init__(self, invoker: AwsApiInvoker, api_config: dict[str, Any], region: str) -> None:
        self._config = api_config
        self._service_name = _required_string(api_config, "serviceName", "preview API")
        transport = _required_string(api_config, "transport", "preview API")
        if transport != "sigv4RestJson":
            raise RegistryApiError("preview API transport must be sigv4RestJson")
        # signingName stays in config for documentation/validation; botocore derives the
        # actual SigV4 signing name from the service model when it builds the client.
        _required_string(api_config, "signingName", "preview API")
        endpoint_url = _preview_endpoint_url(api_config, region)
        self._client = build_control_plane_client(
            session=invoker.session(),
            service_name=self._service_name,
            region=region,
            endpoint_url=endpoint_url,
        )
        self.warnings: list[str] = []
        self._warning_set: set[str] = set()

    def describe_registry(self, *, registry_id: str) -> dict[str, Any]:
        """Return the Preview registry itself (not its records).

        Used to derive the target registry configuration a customer has to re-apply by hand: this tool
        migrates records, and who may read a registry is a decision rather than data to copy.

        Failures are wrapped in :class:`RegistryApiError` like every other call on this client, so a
        caller has one exception type to handle. This used to call the boto3 client directly and let
        a raw ``ClientError`` escape, which meant ``target-config`` reported this one operation
        differently from the rest.
        """
        try:
            response = self._client.get_registry(registryId=registry_id)
        except (ClientError, BotoCoreError) as error:
            raise RegistryApiError(
                f"Preview API call {self._service_name}.getRegistry failed: {error}",
                error_code=_client_error_code(error),
            ) from error
        return _without_response_metadata(response)

    def iter_records(
        self,
        *,
        registry_id: str,
        load_mode: str,
        changed_after: str | None,
    ) -> Iterator[ExtractedPreviewRecord]:
        """Yield every source record, paginating and optionally filtering by change time.

        For ``INCREMENTAL`` loads a record with no updated timestamp is included rather than
        dropped, so a missing timestamp never silently loses data.
        """
        list_operation = _required_string(self._config, "listOperation", "preview API")
        get_operation = _optional_string(self._config.get("getOperation"))
        request_config = _dict(self._config.get("request"), "preview.request")
        response_config = _dict(self._config.get("response"), "preview.response")

        registry_field = _required_string(request_config, "registryIdField", "preview.request")
        page_token_field = _optional_string(request_config.get("pageTokenField"))
        page_size_field = _optional_string(request_config.get("pageSizeField"))
        changed_after_field = _optional_string(request_config.get("changedAfterField"))
        items_path = _required_string(response_config, "itemsPath", "preview.response")
        next_token_path = _optional_string(response_config.get("nextTokenPath"))
        record_path = _optional_string(response_config.get("recordPath"))
        record_id_path = _required_string(response_config, "recordIdPath", "preview.response")
        updated_at_path = _optional_string(response_config.get("updatedAtPath"))

        cutoff = parse_timestamp(changed_after) if load_mode == "INCREMENTAL" and changed_after else None
        base_request: dict[str, Any] = {}
        set_path(base_request, registry_field, registry_id)
        if page_size_field:
            set_path(base_request, page_size_field, int(request_config.get("pageSize", 100)))
        if cutoff and changed_after_field:
            set_path(base_request, changed_after_field, cutoff)

        next_token: Any = None
        seen_tokens: set[str] = set()
        while True:
            request = _copy_request(base_request)
            if next_token is not None and page_token_field:
                set_path(request, page_token_field, next_token)
            response = self._call(
                route_name="list",
                operation=list_operation,
                request=request,
            )
            items = get_path(response, items_path, [])
            if not isinstance(items, list):
                raise RegistryApiError(f"Preview response path {items_path!r} is not an array")

            for summary in items:
                if not isinstance(summary, dict):
                    raise RegistryApiError("Preview list response contains a non-object record")
                record_id = get_path(summary, record_id_path)
                if record_id in (None, ""):
                    raise RegistryApiError(f"Preview list record is missing id at response path {record_id_path!r}")

                if cutoff and get_operation and updated_at_path:
                    summary_updated_at = get_path(summary, updated_at_path)
                    if summary_updated_at is not None and parse_timestamp(summary_updated_at) < cutoff:
                        continue

                record = summary
                if get_operation:
                    get_request: dict[str, Any] = {}
                    set_path(get_request, registry_field, registry_id)
                    set_path(
                        get_request,
                        _required_string(request_config, "recordIdField", "preview.request"),
                        record_id,
                    )
                    get_response = self._call(
                        route_name="get",
                        operation=get_operation,
                        request=get_request,
                    )
                    selected = get_path(get_response, record_path) if record_path else get_response
                    if not isinstance(selected, dict):
                        raise RegistryApiError(f"Preview get response path {record_path!r} is not an object")
                    record = _without_response_metadata(selected)

                if cutoff:
                    updated_at = get_path(record, updated_at_path) if updated_at_path else None
                    if updated_at is None:
                        self._warn(
                            "At least one preview record had no updated timestamp; it was included in "
                            "the incremental extract to avoid data loss."
                        )
                    elif parse_timestamp(updated_at) < cutoff:
                        continue
                yield ExtractedPreviewRecord(record=record, old_record_id=str(record_id))

            next_token = get_path(response, next_token_path) if next_token_path else None
            if not next_token:
                break
            token_key = str(next_token)
            if token_key in seen_tokens:
                raise RegistryApiError("Preview pagination returned a repeated next token")
            seen_tokens.add(token_key)
            if not page_token_field:
                raise RegistryApiError(
                    "Preview response returned a next token but request.pageTokenField is not configured"
                )

    def _call(
        self,
        *,
        route_name: str,
        operation: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the modeled Preview operation for ``route_name`` with SDK kwargs.

        ``request`` already carries the operation's members by their model names
        (``registryId``/``maxResults``/``nextToken`` for list, ``registryId``/``recordId``
        for get), so it is forwarded straight through as keyword arguments.
        """
        method_name = _PREVIEW_OPERATIONS.get(route_name)
        if method_name is None:
            raise RegistryApiError(f"Unsupported preview route {route_name!r}")
        try:
            response = getattr(self._client, method_name)(**request)
        except (ClientError, BotoCoreError) as error:
            raise RegistryApiError(f"Preview API call {self._service_name}.{operation} failed: {error}") from error
        return _without_response_metadata(response)

    def _warn(self, warning: str) -> None:
        if warning not in self._warning_set:
            self._warning_set.add(warning)
            self.warnings.append(warning)


@dataclass(frozen=True)
class LoadResult:
    action: str
    new_record_id: str | None
    # The target record as the service describes it after the write settles. Captured from the status
    # poll that upsert already performs, so reports can compare the Preview record against the
    # real target record without issuing another GetRegistryRecord call.
    record: dict[str, Any] | None = None
    # Anything about *this record's* load a reviewer should see, merged into the record's report
    # warnings. Per-record rather than per-client, because it describes one record's history.
    warnings: tuple[str, ...] = ()


@dataclass
class StatusResult:
    """What reproducing one source status on the target record actually achieved."""

    requested: str
    achieved: str | None
    # The calls made, in order, e.g. ["submitForApproval", "updateStatus=APPROVED"]. Empty when the
    # record already held the requested status, or when nothing could be attempted.
    actions: list[str] = field(default_factory=list)
    reproducible: bool = True
    error: str | None = None

    @property
    def matched(self) -> bool:
        """Whether the target record ended up in the status the source record holds."""
        return self.achieved is not None and self.achieved == self.requested


class TargetNameClaims:
    """Thread-safe claims for the target registry ``(registry, name, recordVersion)`` identity.

    Preview permits multiple records with the same identity while the new version requires it to be unique.
    Keeping this guard independent of the API client lets dry runs enforce the same cross-record
    invariant as live loads without constructing a client or making an AWS call.
    """

    def __init__(
        self,
        claimed: dict[tuple[str, str, str | None], str] | None = None,
        lock: Any | None = None,
    ) -> None:
        # ``TargetRegistryClient`` keeps these established attributes because transport-replacing
        # subclasses initialize them directly. Wrapping caller-owned state preserves that extension
        # point while standalone dry-run guards get fresh state.
        self._claimed = claimed if claimed is not None else {}
        self._lock = lock if lock is not None else threading.Lock()

    def claim(
        self,
        registry_id: str,
        name: str,
        record_version: Any,
        source_record_id: str,
    ) -> None:
        """Reserve one transformed target identity for one Preview source record."""
        key = (registry_id, name, _normalized_version(record_version))
        with self._lock:
            claimed_by = self._claimed.get(key)
            if claimed_by is None:
                self._claimed[key] = source_record_id
                return
            if claimed_by == source_record_id:
                return
        raise RegistryApiError(
            f"Preview records {claimed_by!r} and {source_record_id!r} both migrate to the target registry name "
            f"{name!r}"
            + (f" (recordVersion {record_version!r})" if record_version not in (None, "") else "")
            + f". That identity is already claimed in registry {registry_id}; the new version requires it "
            "to be unique, so loading the second "
            "would overwrite the first. Rename one of them in the source registry, or give them "
            "distinct recordVersions, and re-extract."
        )


class TargetRegistryClient:
    """Model-independent target control-plane client using the baked REST/JSON contract."""

    def __init__(self, invoker: AwsApiInvoker, api_config: dict[str, Any], region: str) -> None:
        self._config = api_config
        self._service_name = _required_string(api_config, "serviceName", "target API")
        # signingName stays in config for documentation/validation; botocore derives the
        # actual SigV4 signing name from the new Registry service model (``agent-registry``).
        _required_string(api_config, "signingName", "target API")
        endpoint_url = target_endpoint_url(api_config, region)
        self._client = build_control_plane_client(
            session=invoker.session(),
            service_name=self._service_name,
            region=region,
            endpoint_url=endpoint_url,
        )
        self._request_config = _dict(api_config.get("request"), "target.request")
        self._response_config = _dict(api_config.get("response"), "target.response")
        self._poll_config = _dict(api_config.get("poll"), "target.poll")
        # Lifecycle status sets are read once here and reused by the poll loop.
        self._in_progress_statuses = _status_set(self._poll_config, "inProgressStatuses", ["CREATING", "UPDATING"])
        self._failure_statuses = _status_set(self._poll_config, "failureStatuses", ["CREATE_FAILED", "UPDATE_FAILED"])
        # Defaults cover every settled record state, so a record the customer already submitted
        # or approved counts as settled and can still be updated by a later incremental run.
        self._success_statuses = _status_set(
            self._poll_config,
            "successStatuses",
            ["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"],
        )
        self._configure_poll_budgets()
        # Read with defaults rather than required config, so the shipped API adapter does not have
        # to change: it is covered by the replay fingerprint, and changing it would invalidate every
        # already-staged run.
        self._conflict_retry_attempts = max(
            1,
            int(self._poll_config.get("conflictRetryAttempts", DEFAULT_CONFLICT_RETRY_ATTEMPTS)),
        )
        self._conflict_retry_delay_seconds = max(
            0.0,
            float(self._poll_config.get("conflictRetryDelaySeconds", DEFAULT_CONFLICT_RETRY_DELAY_SECONDS)),
        )
        # Source-identity index per target registry, built at most once (see _find_existing_by_source).
        # This client is shared by the load stage's worker threads, hence the lock.
        self._source_index_by_registry: dict[str, dict[str, dict[str, Any]]] = {}
        # Guards the three maps here, and is held only for the moment it takes to read or update
        # one -- never across an API call. See _source_index for why that distinction matters.
        self._source_index_lock = threading.Lock()
        # One build lock per registry, so indexing registry A does not block a thread that needs
        # registry B's index (or that only wants to record what it just created).
        self._source_index_build_locks: dict[str, threading.Lock] = {}
        # Identities of records written while a registry's index was still being built. The scan may
        # have already paged past them, so they are folded in when the build finishes rather than
        # dropped -- a dropped identity is a record the next lookup misses and duplicates.
        self._pending_source_identities: dict[str, dict[str, dict[str, Any]]] = {}
        # (registry, name, recordVersion) -> the source record that claimed it (see _claim_name).
        # Keep the map and lock as attributes because transport-replacing subclasses initialize
        # them directly; the reusable guard wraps them so dry runs can enforce the same invariant.
        self._claimed_names: dict[tuple[str, str, str | None], str] = {}
        self._claimed_names_lock = threading.Lock()
        self._name_claims = TargetNameClaims(self._claimed_names, self._claimed_names_lock)
        # (registry, target recordId) -> the source record that claimed it (see _claim_target_record).
        # _claimed_names is keyed on the name we *ask* for; this is keyed on the record we actually
        # resolve to, which is the only thing that catches a collision the service itself creates.
        self._claimed_targets: dict[tuple[str, str], str] = {}
        self._claimed_targets_lock = threading.Lock()

    def _bind_claim_guards(
        self,
        name_claims: TargetNameClaims,
        claimed_targets: dict[tuple[str, str], str],
        claimed_targets_lock: Any,
    ) -> None:
        """Share final write guards across access routes to the same canonical target."""
        self._name_claims = name_claims
        self._claimed_targets = claimed_targets
        self._claimed_targets_lock = claimed_targets_lock

    def _configure_poll_budgets(self) -> None:
        """Resolve and validate the poll budgets from ``target.poll``, once.

        Separate from ``__init__`` so a subclass that replaces the transport (the test doubles do)
        can adopt the real budgets with one call instead of hand-copying the fields -- copying them
        is how a double drifts from the class it stands in for.

        Validating here rather than inside the poll loops means a bad adapter fails before any
        record is written, instead of part-way through a load on the first record that needed
        polling.
        """
        self._poll_attempts = int(self._poll_config.get("maxAttempts", DEFAULT_POLL_ATTEMPTS))
        self._poll_interval_seconds = float(self._poll_config.get("intervalSeconds", DEFAULT_POLL_INTERVAL_SECONDS))
        if self._poll_attempts < 1 or self._poll_interval_seconds < 0:
            raise RegistryApiError("Target poll settings must use maxAttempts >= 1 and intervalSeconds >= 0")
        # Status transitions settle faster than creates, so they get their own smaller budget --
        # capped by the record-level budget so shortening that shortens this too. See
        # DEFAULT_STATUS_POLL_ATTEMPTS.
        self._status_poll_attempts = max(
            1,
            min(
                self._poll_attempts,
                int(self._poll_config.get("statusMaxAttempts", DEFAULT_STATUS_POLL_ATTEMPTS)),
            ),
        )

    def upsert(
        self,
        *,
        registry_id: str,
        record: dict[str, Any],
        source_record_id: str | None = None,
        known_record_id: str | None = None,
    ) -> LoadResult:
        """Idempotently create or update ``record`` in the target registry and return the mapping.

        Matches an existing record by the target recordId a previous run recorded for this source
        record first, then by name(+recordVersion), then -- for source-backed records whose name the
        service may rewrite on synchronization -- by descriptor-source identity. Waits for the
        record to reach a terminal state before returning.

        ``source_record_id`` is the Preview record this came from. When given, the name is claimed
        for it: a second, *different* source record arriving under the same name is refused rather
        than allowed to update -- and so overwrite -- the first one's content. Re-processing the same
        source record is still idempotent.

        ``known_record_id`` is the target record this same source record was migrated to by an earlier
        run, from the persisted id map. It takes precedence over the name because it is the only
        identifier that survives the source record being renamed.
        """
        name = _required_string(record, "name", "target record")
        record_version = record.get("recordVersion")
        if source_record_id:
            self._claim_name(registry_id, name, record_version, source_record_id)
        load_warnings: list[str] = []
        # Identity first: this source record was already migrated, and *that* target record is the one to
        # update, whatever it is called now. Checked before the name so that renaming a record in
        # Preview updates its existing target record instead of creating a second one and orphaning the
        # first -- a rename changes the name on the source side only, and nothing else about the
        # record carries its identity across runs.
        existing = None
        if known_record_id:
            existing = self._find_existing_by_id(
                registry_id=registry_id,
                record_id=known_record_id,
                warnings=load_warnings,
            )
        # A name match is treated as "the same record again", which is what makes a re-run an upsert
        # rather than a duplicator. Its limit: a record already in the target under this name that
        # this migration did not create -- a dual-write record, or one migrated earlier from another
        # source registry -- is indistinguishable from that, so it is updated. Closing this needs
        # provenance on the record itself; see "Swapping in the official service model" in docs/development.md,
        # which waits on `metadata` being accepted by CreateRegistryRecord.
        if existing is None:
            existing = self._find_existing(
                registry_id=registry_id,
                name=name,
                record_version=record_version,
            )
        if existing is None and _descriptor_sources(record.get("descriptors")):
            existing = self._find_existing_by_source(
                registry_id=registry_id,
                record=record,
            )
        if existing is not None:
            existing_record_id = get_path(
                existing,
                _required_string(self._response_config, "recordIdPath", "target.response"),
            )
            if existing_record_id in (None, ""):
                raise RegistryApiError("Existing target record is missing its recordId")
            record_id = str(existing_record_id)
            if source_record_id:
                self._claim_target_record(registry_id, record_id, source_record_id)
            current = self._get_record(registry_id=registry_id, record_id=record_id)
            current_status = _required_string(current, "status", "target GetRegistryRecord response")
            if self._is_in_progress(current_status):
                current = self._wait_for_terminal(
                    registry_id=registry_id,
                    record_id=record_id,
                    expected_record=None,
                )
            elif self._is_failure(current_status):
                reason = _optional_string(current.get("statusReason")) or "No status reason returned"
                raise RegistryApiError(
                    f"Existing target record {record_id} is in failure status {current_status}: {reason}",
                    record_id=record_id,
                )
            if _record_matches_desired(current, record):
                return LoadResult(
                    action="existing",
                    new_record_id=record_id,
                    record=current,
                    warnings=tuple(load_warnings),
                )

            response = self._call(
                route_name="update",
                registry_id=registry_id,
                record_id=record_id,
                body=_build_update_body(record, current),
            )
            update_status = _required_string(response, "status", "target update response")
            if update_status != "UPDATING":
                raise RegistryApiError(
                    f"Target update returned status {update_status!r}; expected 'UPDATING'",
                    record_id=record_id,
                )
            returned_id = get_path(response, "recordId", record_id)
            loaded = self._wait_for_terminal(
                registry_id=registry_id,
                record_id=str(returned_id),
                expected_record=record,
            )
            self._remember_source_identity(registry_id, loaded)
            return LoadResult(
                action="updated",
                new_record_id=str(returned_id),
                record=loaded,
                warnings=tuple(load_warnings),
            )

        create_body = _build_create_body(record)
        create_body["clientToken"] = _client_token(registry_id, name, record_version)
        response = self._call(
            route_name="create",
            registry_id=registry_id,
            record_id=None,
            body=create_body,
        )
        create_status = _required_string(response, "status", "target create response")
        if create_status != "CREATING":
            raise RegistryApiError(f"Target create returned status {create_status!r}; expected 'CREATING'")
        record_arn_path = _required_string(
            self._response_config,
            "recordArnPath",
            "target.response",
        )
        record_arn = get_path(response, record_arn_path)
        record_id = _record_id_from_arn(record_arn)
        if not record_id:
            raise RegistryApiError(
                "Target create returned no usable recordArn; the old-to-new ID mapping cannot be completed"
            )
        if source_record_id:
            # Claim it even though we just created it: the service may rename a source-backed record
            # during synchronization, and a later source record resolving onto this one by source
            # identity has to be refused rather than allowed to update it.
            self._claim_target_record(registry_id, record_id, source_record_id)
        try:
            loaded = self._wait_for_terminal(
                registry_id=registry_id,
                record_id=record_id,
                expected_record=record,
            )
        except RegistryApiError as error:
            # The record was created; only settling failed. Anything raised from here on -- a
            # CREATE_FAILED status, a timeout, or a failed status read -- describes a record that
            # exists in the target registry, so make sure its id reaches the caller's reports.
            if error.record_id is None:
                error.record_id = record_id
            raise
        # Keep the index truthful about what this run has written, so a repeated record in the
        # staged data is recognised instead of created twice.
        self._remember_source_identity(registry_id, loaded)
        return LoadResult(
            action="created",
            new_record_id=record_id,
            record=loaded,
            warnings=tuple(load_warnings),
        )

    def apply_status(
        self,
        *,
        registry_id: str,
        record_id: str,
        desired_status: str,
        current_status: str | None = None,
        reason: str | None = None,
    ) -> StatusResult:
        """Drive a freshly loaded target record to the status its Preview record holds.

        target creates every record in DRAFT regardless of the source, so an approved Preview record
        would arrive invisible to the data plane -- `ListDiscoverableRegistryRecords` and search only
        return approved records. Reproducing the status is therefore part of migrating the record,
        not an optional follow-up.

        The ladder mirrors the target state machine rather than assuming a single call gets there:

        * DRAFT needs nothing -- that is where a created record already is.
        * PENDING_APPROVAL is one ``SubmitRegistryRecordForApproval``. If the target registry carries
          ``autoApprovalRules: [APPROVE_ALL]`` the service immediately promotes it to APPROVED; that
          is the registry's own policy overriding the source state, so it is reported as a divergence
          rather than fought.
        * APPROVED is submit, then -- only if the registry did not auto-approve --
          ``UpdateRegistryRecordStatus``.
        * REJECTED and DEPRECATED are attempted directly first, since they are ordinary status
          transitions, and fall back to submitting first for a service that requires the record to
          have left DRAFT.

        Failures are returned, not raised: the record itself is loaded and correct, and losing that
        because a status transition was refused would be worse than reporting the gap. The caller
        surfaces it per record and in the run's approval summary.
        """
        requested = (desired_status or "").strip().upper()
        result = StatusResult(requested=requested, achieved=current_status)
        if not requested or requested == "DRAFT":
            # Nothing to do, and nothing worth reporting: a created record is already DRAFT.
            return result
        if requested not in REPRODUCIBLE_STATUSES:
            result.reproducible = False
            return result
        if current_status and current_status.upper() == requested:
            return result

        try:
            if requested in {"PENDING_APPROVAL", "APPROVED"}:
                observed = self._submit_for_approval(registry_id=registry_id, record_id=record_id)
                result.actions.append("submitForApproval")
                if requested == "APPROVED" and observed != "APPROVED":
                    observed = self._set_status(
                        registry_id=registry_id,
                        record_id=record_id,
                        status="APPROVED",
                        reason=reason,
                    )
                    result.actions.append("updateStatus=APPROVED")
                result.achieved = observed
                return result

            # REJECTED / DEPRECATED.
            try:
                result.achieved = self._set_status(
                    registry_id=registry_id,
                    record_id=record_id,
                    status=requested,
                    reason=reason,
                )
                result.actions.append(f"updateStatus={requested}")
                return result
            except RegistryApiError as direct_error:
                # A service that only allows this transition out of PENDING_APPROVAL: submit, then
                # retry once. A second failure is reported -- and reported together with this first
                # one, because "the direct transition was refused because X, and after submitting it
                # was refused because Y" is the whole diagnosis. Reporting only the second error
                # (which is what this used to do) throws away the reason the fallback was needed.
                first_refusal = str(direct_error)
                try:
                    self._submit_for_approval(registry_id=registry_id, record_id=record_id)
                    result.actions.append("submitForApproval")
                    result.achieved = self._set_status(
                        registry_id=registry_id,
                        record_id=record_id,
                        status=requested,
                        reason=reason,
                    )
                except RegistryApiError as fallback_error:
                    raise RegistryApiError(
                        f"{fallback_error} (after a direct transition to {requested} was refused: {first_refusal})",
                        record_id=fallback_error.record_id or record_id,
                        error_code=fallback_error.error_code,
                    ) from fallback_error
                result.actions.append(f"updateStatus={requested}")
                return result
        except RegistryApiError as error:
            result.error = str(error)
            result.achieved = self._read_status(registry_id=registry_id, record_id=record_id)
            return result

    def _submit_for_approval(self, *, registry_id: str, record_id: str) -> str | None:
        """Submit a record and return the status it settles in (PENDING_APPROVAL, or APPROVED)."""
        self._call_status_transition(
            route_name="submitForApproval",
            registry_id=registry_id,
            record_id=record_id,
            body=None,
        )
        # The submit response reports the immediate status, but auto-approval lands asynchronously,
        # so the settled status comes from a read rather than from the 202.
        return self._wait_for_status(
            registry_id=registry_id,
            record_id=record_id,
            expected={"PENDING_APPROVAL", "APPROVED"},
        )

    def _set_status(
        self,
        *,
        registry_id: str,
        record_id: str,
        status: str,
        reason: str | None,
    ) -> str | None:
        """Set a record's status explicitly and wait for the change to be observable."""
        body: dict[str, Any] = {"status": status}
        if reason:
            body["statusReason"] = reason[:1024]
        self._call_status_transition(
            route_name="updateStatus",
            registry_id=registry_id,
            record_id=record_id,
            body=body,
        )
        return self._wait_for_status(
            registry_id=registry_id,
            record_id=record_id,
            expected={status},
        )

    def _call_status_transition(
        self,
        *,
        route_name: str,
        registry_id: str,
        record_id: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Invoke a status-transition route, retrying the conflict the service asks us to retry.

        A migration creates a record and immediately drives it to its source status, which is
        exactly the shape of request the service answers with ``ConflictException: ... Concurrent update
        detected. Please retry.`` -- the previous write on that record has not settled yet. It is
        transient and self-correcting: observed live, a second run of the same migration put the
        affected records into their status with no other change, which is the definition of
        something that should have been retried in the first place.

        Retrying is safe here in a way it would not be for an arbitrary call: both transitions name
        one specific end state rather than describing a delta, and the caller confirms the state by
        reading the record afterwards. Only ``ConflictException`` is retried, so a real rejection
        (a transition the state machine does not allow) still surfaces immediately.

        botocore's own adaptive retries do not cover this: a 409 conflict is not in its retryable
        set, which is throttling and 5xx.
        """
        delay_seconds = self._conflict_retry_delay_seconds
        for attempt in range(1, self._conflict_retry_attempts + 1):
            try:
                return self._call(
                    route_name=route_name,
                    registry_id=registry_id,
                    record_id=record_id,
                    body=body,
                )
            except RegistryApiError as error:
                if error.error_code != "ConflictException" or attempt >= self._conflict_retry_attempts:
                    raise
                LOGGER.info(
                    "Target %s on record %s hit a concurrent-update conflict (attempt %d of %d); retrying in %.1fs",
                    route_name,
                    record_id,
                    attempt,
                    self._conflict_retry_attempts,
                    delay_seconds,
                )
                if delay_seconds > 0:
                    _POLL_WAIT.wait(delay_seconds)
                delay_seconds *= 2
        # Unreachable: the loop either returns or re-raises on its final attempt.
        raise RegistryApiError(
            f"Target {route_name} exhausted its conflict retries for record {record_id}",
            record_id=record_id,
        )

    def _wait_for_status(
        self,
        *,
        registry_id: str,
        record_id: str,
        expected: set[str],
    ) -> str | None:
        """Poll until the record shows one of ``expected``, returning the last status seen.

        Deliberately does not raise on a timeout: the status transition is asynchronous and this
        returns what the record actually holds, which is what the report needs to state.
        """
        attempts = self._status_poll_attempts
        status: str | None = None
        for attempt in range(attempts):
            status = self._read_status(registry_id=registry_id, record_id=record_id)
            if status in expected:
                return status
            if status and self._is_failure(status):
                return status
            if attempt + 1 < attempts:
                _POLL_WAIT.wait(self._poll_interval_seconds)
        return status

    def _read_status(self, *, registry_id: str, record_id: str) -> str | None:
        """Best-effort current status, used for reporting after a transition or a failure."""
        try:
            record = self._get_record(registry_id=registry_id, record_id=record_id)
        except RegistryApiError:
            return None
        return _optional_string(record.get("status"))

    def list_records_page(self, *, registry_id: str, page_size: int = 1) -> dict[str, Any]:
        """List a single small page of records.

        Used by pre-flight validation as the cheapest call that proves the registry exists and the
        configured credentials may read it.
        """
        page_size_field = _required_string(self._request_config, "pageSizeField", "target.request")
        return self._call(
            route_name="list",
            registry_id=registry_id,
            record_id=None,
            body={page_size_field: max(1, int(page_size))},
        )

    def _find_existing_by_id(
        self,
        *,
        registry_id: str,
        record_id: str,
        warnings: list[str],
    ) -> dict[str, Any] | None:
        """Fetch the target record a previous run migrated this source record to, or None if it is gone.

        A recorded record that no longer exists is not an error: somebody may have deleted it in the
        target registry between runs, and the right answer then is to fall back to matching by name
        and, failing that, to create it again. Any other failure propagates -- an id map entry that
        cannot be read because of, say, a permissions problem must not be quietly downgraded into
        "create a duplicate".
        """
        try:
            return self._get_record(registry_id=registry_id, record_id=record_id)
        except RegistryApiError as error:
            if error.error_code == "ResourceNotFoundException":
                warnings.append(
                    f"Target record {record_id}, recorded by an earlier migration of this source "
                    "record, no longer exists in the target registry; matched by name instead, and "
                    "created again if there was no match."
                )
                return None
            raise

    def _find_existing(
        self,
        *,
        registry_id: str,
        name: str,
        record_version: Any,
    ) -> dict[str, Any] | None:
        """Find the unique existing record matching ``name`` and ``recordVersion``, if any."""
        filters_field = _required_string(self._request_config, "filtersField", "target.request")
        page_size_field = _required_string(self._request_config, "pageSizeField", "target.request")
        page_token_field = _required_string(self._request_config, "pageTokenField", "target.request")
        items_path = _required_string(self._response_config, "itemsPath", "target.response")
        next_token_path = _required_string(
            self._response_config,
            "nextTokenPath",
            "target.response",
        )
        record_name_path = _required_string(
            self._response_config,
            "recordNamePath",
            "target.response",
        )
        record_version_path = _required_string(
            self._response_config,
            "recordVersionPath",
            "target.response",
        )
        base_request: dict[str, Any] = {
            filters_field: [{"name": "name", "values": [name]}],
            page_size_field: int(self._request_config.get("pageSize", 100)),
        }
        matches: list[dict[str, Any]] = []
        next_token: Any = None
        seen_tokens: set[str] = set()

        while True:
            request = copy.deepcopy(base_request)
            if next_token is not None:
                request[page_token_field] = next_token
            response = self._call(
                route_name="list",
                registry_id=registry_id,
                record_id=None,
                body=request,
            )
            items = get_path(response, items_path, [])
            if not isinstance(items, list):
                raise RegistryApiError(f"Target list response path {items_path!r} is not an array")
            for item in items:
                if not isinstance(item, dict):
                    raise RegistryApiError("Target list response contains a non-object record")
                item_name = get_path(item, record_name_path)
                if item_name in (None, ""):
                    raise RegistryApiError(f"Target list result is missing name at response path {record_name_path!r}")
                if str(item_name) != name:
                    continue
                item_version = get_path(item, record_version_path)
                if _versions_equal(item_version, record_version):
                    matches.append(item)

            next_token = get_path(response, next_token_path)
            if not next_token:
                break
            token_key = str(next_token)
            if token_key in seen_tokens:
                raise RegistryApiError("Target list pagination returned a repeated next token")
            seen_tokens.add(token_key)

        if len(matches) > 1:
            raise RegistryApiError(
                f"Target registry returned multiple records for name={name!r}, recordVersion={record_version!r}"
            )
        return matches[0] if matches else None

    def _find_existing_by_source(
        self,
        *,
        registry_id: str,
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Find a prior record by descriptor-source identity when the target name was rewritten.

        Target URL synchronization can replace the requested name, so a source-backed record whose name
        lookup missed is matched on its exact descriptor sources plus recordType and recordVersion.

        The registry is indexed **once per target registry**, not once per record. Scanning per
        record made this quadratic: with the registry filling up as the load progressed, record *k*
        read the *k-1* records already created, so a 1,000-record migration issued on the order of
        half a million GetRegistryRecord calls. One pass costs one list plus one Get per record that
        already exists -- and nothing at all on a first load, where the registry is empty.
        """
        identity = _source_identity(record)
        if identity is None:
            return None
        return self._source_index(registry_id).get(identity)

    def _claim_name(
        self,
        registry_id: str,
        name: str,
        record_version: Any,
        source_record_id: str,
    ) -> None:
        """Apply the live client's backstop for a duplicate transformed target identity."""
        name_claims = getattr(self, "_name_claims", None)
        if name_claims is None:
            # Transport-replacing subclasses may deliberately bypass ``__init__`` while retaining
            # the established claim map and lock. Adapt those lazily; concurrent wrappers still
            # share the same lock and map, so exactly one claimant wins.
            name_claims = TargetNameClaims(self._claimed_names, self._claimed_names_lock)
            self._name_claims = name_claims
        name_claims.claim(registry_id, name, record_version, source_record_id)

    def _claim_target_record(
        self,
        registry_id: str,
        record_id: str,
        source_record_id: str,
    ) -> None:
        """Reserve one target record for one source record, refusing a second claimant.

        ``_claim_name`` guards the name we *ask* the service for, which is not enough: target URL
        synchronization overwrites a record's name and recordVersion with the values from the fetched
        document. Several source records syncing from one upstream therefore ask for distinct names,
        pass the name claim, miss the name lookup (the service renamed what we created), and are then
        all matched to that one record by ``_find_existing_by_source`` -- because the source identity
        deliberately excludes the name. Without this guard they each "succeed": the first is created
        and the rest update it, so N source records silently collapse into one target record and the
        id-crosswalk records the loss as N successful migrations.

        Observed live: four Preview records (a renamed one, a content-edited one, a
        description-edited one, and one with no description) all resolved onto a single target record.

        Re-processing the same source record stays idempotent, which is what makes a re-run an
        upsert rather than a duplicator.
        """
        key = (registry_id, record_id)
        with self._claimed_targets_lock:
            claimed_by = self._claimed_targets.get(key)
            if claimed_by is None:
                self._claimed_targets[key] = source_record_id
                return
            if claimed_by == source_record_id:
                return
        raise RegistryApiError(
            f"Preview records {claimed_by!r} and {source_record_id!r} both resolve to target record "
            f"{record_id} in registry {registry_id}, so loading the second would overwrite the "
            "first. This happens when several source records synchronize from one URL: the service replaces "
            "each record's name and version with the values from the fetched document, so they "
            "become the same record. Point them at distinct URLs, or migrate only one of them and "
            "recreate the others by hand, then re-extract.",
            record_id=record_id,
        )

    def _source_index(self, registry_id: str) -> dict[str, dict[str, Any]]:
        """Return the source-identity index for ``registry_id``, building it at most once.

        Concurrent loaders share this client, so the first thread to need a registry's index builds
        it while the others wait -- there is no way to use an index before it exists.

        What the waiting is careful about is *scope*. Indexing costs one list plus one Get per
        existing record, so for a populated target registry it is thousands of API calls. Holding
        the map lock across all of that (which is what this used to do) blocked every worker thread
        in the pool, including threads that wanted a different registry's index and threads that
        only wanted to record the identity of a record they had just created. So the build happens
        under a per-registry build lock, and the map lock is taken only to read or publish the map.
        """
        with self._source_index_lock:
            index = self._source_index_by_registry.get(registry_id)
            if index is not None:
                return index
            build_lock = self._source_index_build_locks.setdefault(registry_id, threading.Lock())

        with build_lock:
            # Re-check: another thread may have finished the build while this one waited.
            with self._source_index_lock:
                index = self._source_index_by_registry.get(registry_id)
                if index is not None:
                    return index
            built = self._build_source_index(registry_id)
            with self._source_index_lock:
                # Fold in anything written while the scan was running. The scan may have paged past
                # those records before they existed, and _remember_source_identity had no index to
                # put them in, so without this they would be invisible to the next lookup and
                # created a second time.
                built.update(self._pending_source_identities.pop(registry_id, {}))
                self._source_index_by_registry[registry_id] = built
            return built

    def _remember_source_identity(self, registry_id: str, loaded: dict[str, Any]) -> None:
        """Add a just-written record to the index, so a repeat within this run is not duplicated.

        When the index for this registry has not been built yet the identity is held aside rather
        than dropped: either a later build folds it in, or no lookup ever needs it. Both are cheap;
        losing it is not, because the next record with the same identity would be created again.
        """
        identity = _source_identity(loaded)
        if identity is None:
            return
        with self._source_index_lock:
            index = self._source_index_by_registry.get(registry_id)
            if index is not None:
                index[identity] = loaded
            else:
                self._pending_source_identities.setdefault(registry_id, {})[identity] = loaded

    def _build_source_index(self, registry_id: str) -> dict[str, dict[str, Any]]:
        """Index every existing source-backed record in the registry by its source identity."""
        index: dict[str, dict[str, Any]] = {}
        scanned = 0
        for summary in self._iter_all_records(registry_id):
            record_id = get_path(
                summary,
                _required_string(self._response_config, "recordIdPath", "target.response"),
            )
            if record_id in (None, ""):
                raise RegistryApiError("Target list result is missing recordId while indexing the target registry")
            # List summaries carry no descriptors, so the sources are only visible on the record
            # itself. This is the one Get per existing record that the index pays for.
            current = self._get_record(registry_id=registry_id, record_id=str(record_id))
            scanned += 1
            identity = _source_identity(current)
            if identity is None:
                continue
            if identity in index:
                raise RegistryApiError(
                    "Target registry holds multiple records with the same descriptor source identity, "
                    f"recordType and recordVersion (records {index[identity].get('recordId')!r} "
                    f"and {current.get('recordId')!r}); resolve the duplicate before loading"
                )
            index[identity] = current
        LOGGER.info(
            "Indexed %d source-backed record(s) from %d existing record(s) in target registry %s",
            len(index),
            scanned,
            registry_id,
        )
        return index

    def _iter_all_records(self, registry_id: str) -> Iterator[dict[str, Any]]:
        """Yield every record summary in the registry, following pagination."""
        page_size_field = _required_string(self._request_config, "pageSizeField", "target.request")
        page_token_field = _required_string(self._request_config, "pageTokenField", "target.request")
        items_path = _required_string(self._response_config, "itemsPath", "target.response")
        next_token_path = _required_string(
            self._response_config,
            "nextTokenPath",
            "target.response",
        )
        base_request: dict[str, Any] = {
            page_size_field: int(self._request_config.get("pageSize", 100)),
        }
        next_token: Any = None
        seen_tokens: set[str] = set()

        while True:
            request = copy.deepcopy(base_request)
            if next_token is not None:
                request[page_token_field] = next_token
            response = self._call(
                route_name="list",
                registry_id=registry_id,
                record_id=None,
                body=request,
            )
            items = get_path(response, items_path, [])
            if not isinstance(items, list):
                raise RegistryApiError(f"Target list response path {items_path!r} is not an array")
            for item in items:
                if not isinstance(item, dict):
                    raise RegistryApiError("Target list response contains a non-object record")
                yield item

            next_token = get_path(response, next_token_path)
            if not next_token:
                break
            token_key = str(next_token)
            if token_key in seen_tokens:
                raise RegistryApiError("Target list pagination returned a repeated next token")
            seen_tokens.add(token_key)

    def _get_record(self, *, registry_id: str, record_id: str) -> dict[str, Any]:
        return self._call(
            route_name="get",
            registry_id=registry_id,
            record_id=record_id,
            body=None,
        )

    def _wait_for_terminal(
        self,
        *,
        registry_id: str,
        record_id: str,
        expected_record: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Poll a record until it reaches a success status showing the desired representation.

        When ``expected_record`` is given, a success status is only accepted once the record's
        observable fields match it, so a stale pre-update read never counts as completion.
        """
        # Resolved and validated in __init__, so a bad adapter fails before any record is written.
        max_attempts = self._poll_attempts
        interval_seconds = self._poll_interval_seconds

        for attempt in range(max_attempts):
            response = self._get_record(registry_id=registry_id, record_id=record_id)
            status = _optional_string(response.get("status"))
            if not status:
                raise RegistryApiError("Target GetRegistryRecord response is missing status")
            if status in self._success_statuses:
                if expected_record is None or _record_matches_desired(response, expected_record):
                    return response
                # A terminal response with stale pre-update fields must not satisfy the async
                # completion check. Continue until the desired representation is observable.
            elif status in self._failure_statuses or status.endswith("_FAILED"):
                reason = _optional_string(response.get("statusReason")) or "No status reason returned"
                raise RegistryApiError(
                    f"Target record {record_id} reached failure status {status}: {reason}",
                    record_id=record_id,
                )
            elif status not in self._in_progress_statuses:
                raise RegistryApiError(
                    f"Target record {record_id} returned unknown lifecycle status {status!r}",
                    record_id=record_id,
                )
            if attempt + 1 < max_attempts:
                _POLL_WAIT.wait(interval_seconds)

        raise RegistryApiError(
            f"Timed out waiting for target record {record_id} to reach a settled state showing the "
            f"requested content "
            f"after {max_attempts} status checks",
            record_id=record_id,
        )

    def _is_in_progress(self, status: Any) -> bool:
        return str(status) in self._in_progress_statuses

    def _is_failure(self, status: Any) -> bool:
        normalized = str(status)
        return normalized in self._failure_statuses or normalized.endswith("_FAILED")

    def _call(
        self,
        *,
        route_name: str,
        registry_id: str,
        record_id: str | None,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Invoke the modeled target operation for ``route_name``.

        ``registryId``/``recordId`` are the path members; ``body`` carries the remaining
        modeled members (the create/update payload or the list filters + pagination). They
        are merged into one kwargs mapping because botocore routes each member to the URI or
        the JSON body per the service model.
        """
        method_name = _TARGET_OPERATIONS.get(route_name)
        if method_name is None:
            raise RegistryApiError(f"Unsupported target route {route_name!r}")
        params: dict[str, Any] = dict(body) if body else {}
        params["registryId"] = registry_id
        if record_id is not None:
            params["recordId"] = record_id
        try:
            response = getattr(self._client, method_name)(**params)
        except (ClientError, BotoCoreError) as error:
            raise RegistryApiError(
                f"Target API call {self._service_name}.{route_name} failed: {error}",
                error_code=_client_error_code(error),
            ) from error
        return _without_response_metadata(response)


# Target API contract tables: which recordTypes each primary is valid for, which additionalData
# children and which descriptors may carry a source, and which may carry a version.
_FINAL_PRIMARY_RECORD_TYPES: dict[str, set[str]] = {
    "a2aAgentCard": {"AGENT"},
    "mcpServer": {"AGENT", "MCP"},
    "agentSkillsDefinition": {"SKILL"},
    # No ``agentSkillsMd``: the live service answers a record that uses one with "Exactly one
    # valid descriptor is allowed for record type SKILL. Valid descriptors: [agentSkillsDefinition,
    # custom]". Accepting it here would let a dry run PASS a body the service then refuses. The
    # transform normalizes the Preview markdown-only shape away before it reaches this module.
    "custom": {"AGENT", "MCP", "SKILL", "CUSTOM"},
}
_FINAL_ADDITIONAL_DATA: dict[str, set[str]] = {
    "mcpServer": {"tools"},
    "agentSkillsDefinition": {"skillMd"},
}
_FINAL_SOURCE_DESCRIPTORS = {"a2aAgentCard", "mcpServer", "skillMd"}
# The one primary that may omit ``data``, mapping it to the additionalData child that carries the
# content instead. A markdown-only skill has nowhere else to go: the new version has no agentSkillsMd primary, and
# it parses every ``data`` as JSON, which Markdown is not. Every other descriptor requires ``data``.
_FINAL_CONTENT_IN_ADDITIONAL_DATA = {"agentSkillsDefinition": "skillMd"}
_FINAL_VERSIONED_DESCRIPTORS = {
    "a2aAgentCard",
    "mcpServer",
    "agentSkillsDefinition",
    "tools",
    "skillMd",
}

# Maximum lengths for the target record's string fields.
#
# Maintained here rather than read from the new Registry service model, because the model does not supply
# them: `name`, `displayName`, `description` and
# `recordVersion` are all the bare `String` shape with no `min`/`max`/`pattern`, and `descriptors` is
# a `Document`. So botocore validates none of it, and this module is the only thing standing between
# a dry run that passes and a live load that does not. Replace these with the model's own traits when
# the official service model ships (see "Swapping in the official service model" in docs/development.md).
#
# The values are taken from the *Preview* model's own traits, which is the only defensible source
# for them: this tool copies Preview records into the target registry, so any bound tighter than what Preview accepts
# rejects records that demonstrably exist. Preview publishes Description max 4096 and
# RegistryRecordVersion max 255 (RegistryRecordName and the display name are 255), and the seed
# fixtures deliberately create records at exactly those boundaries -- so guessing lower here turns a
# migration that worked into one that refuses its own test data before the service ever sees it.
_TARGET_FIELD_MAX_LENGTHS = {
    "name": 255,
    "displayName": 255,
    "description": 4096,
    "recordVersion": 255,
}


def _validate_length(field: str, value: str) -> None:
    limit = _TARGET_FIELD_MAX_LENGTHS.get(field)
    if limit is not None and len(value) > limit:
        raise RegistryApiError(f"Target record {field} must be at most {limit} characters, got {len(value)}")


def _build_create_body(record: dict[str, Any]) -> dict[str, Any]:
    """Validate a transformed record and project it to a target CreateRegistryRecord body."""
    validate_target_request(record)
    body: dict[str, Any] = {
        "name": copy.deepcopy(record["name"]),
        "displayName": copy.deepcopy(record["displayName"]),
        "recordType": copy.deepcopy(record["recordType"]),
        "descriptors": copy.deepcopy(record["descriptors"]),
    }
    for field_name in ("description", "recordVersion"):
        if field_name in record:
            body[field_name] = copy.deepcopy(record[field_name])
    return body


def validate_target_request(record: dict[str, Any]) -> None:
    """Enforce the target create contract on a record before it is sent to the service.

    Public because a dry run has to apply exactly the same rules a live load would. This is the
    stricter of the two validators -- the transform checks what it can produce, this checks what
    the service will accept -- so if it only ran on the live path, a dry run could pass and the
    load then fail, which would defeat the point of staging.

    The length bounds are maintained here by hand, and have to be: the target service model
    types every one of these members as a bare ``String`` with no ``min``, ``max`` or ``pattern``, so
    botocore validates none of them. See :data:`_TARGET_FIELD_MAX_LENGTHS`.
    """
    name = _required_string(record, "name", "target record")
    display_name = _required_string(record, "displayName", "target record")
    record_type = _required_string(record, "recordType", "target record")
    _validate_length("name", name)
    _validate_length("displayName", display_name)
    if record_type not in {"AGENT", "MCP", "SKILL", "CUSTOM"}:
        raise RegistryApiError(f"Unsupported target recordType {record_type!r}")
    descriptors = record.get("descriptors")
    if not isinstance(descriptors, dict) or len(descriptors) != 1:
        raise RegistryApiError("Target record requires exactly one primary descriptor")
    primary_key, descriptor = next(iter(descriptors.items()))
    allowed_types = _FINAL_PRIMARY_RECORD_TYPES.get(str(primary_key))
    if allowed_types is None or record_type not in allowed_types:
        raise RegistryApiError(
            f"Target primary descriptor {primary_key!r} is incompatible with recordType {record_type!r}"
        )
    _validate_final_descriptor(str(primary_key), descriptor, allow_additional=True)
    for field_name in ("description", "recordVersion"):
        value = record.get(field_name)
        if field_name in record:
            if not isinstance(value, str) or not value:
                raise RegistryApiError(f"Target record {field_name} must be a non-empty string when supplied")
            # Bounded for the same reason name and displayName are: an over-long value passed a dry
            # run and then failed the live load, which is precisely the outcome staging exists to
            # rule out. These two were unbounded.
            _validate_length(field_name, value)


def _validate_final_descriptor(
    descriptor_key: str,
    descriptor: Any,
    *,
    allow_additional: bool,
) -> None:
    """Validate a single target descriptor's fields, source placement, and additionalData."""
    if not isinstance(descriptor, dict):
        raise RegistryApiError(f"Target descriptor {descriptor_key!r} must be an object")
    unsupported = set(descriptor) - {"data", "dataSchemaVersion", "source", "additionalData"}
    if unsupported:
        raise RegistryApiError(
            f"Target descriptor {descriptor_key!r} has unsupported fields: "
            + ", ".join(sorted(str(value) for value in unsupported))
        )
    data = descriptor.get("data")
    if not _content_lives_in_additional_data(descriptor_key, descriptor, allow_additional) and (
        not isinstance(data, str) or not data
    ):
        raise RegistryApiError(f"Target descriptor {descriptor_key!r} requires non-empty string data")
    version = descriptor.get("dataSchemaVersion")
    if "dataSchemaVersion" in descriptor:
        if descriptor_key not in _FINAL_VERSIONED_DESCRIPTORS:
            raise RegistryApiError(f"Target descriptor {descriptor_key!r} does not support dataSchemaVersion")
        if not isinstance(version, str) or not version:
            raise RegistryApiError(f"Target descriptor {descriptor_key!r} dataSchemaVersion must be a non-empty string")
    source = descriptor.get("source")
    if source is not None:
        if descriptor_key not in _FINAL_SOURCE_DESCRIPTORS:
            raise RegistryApiError(f"Target descriptor {descriptor_key!r} does not support source")
        _validate_final_source(source, descriptor_key)
    additional = descriptor.get("additionalData")
    if additional is None:
        return
    if not allow_additional or not isinstance(additional, dict):
        raise RegistryApiError(f"Target descriptor {descriptor_key!r} has invalid additionalData")
    allowed_children = _FINAL_ADDITIONAL_DATA.get(descriptor_key, set())
    unsupported_children = set(additional) - allowed_children
    if unsupported_children:
        raise RegistryApiError(
            f"Target descriptor {descriptor_key!r} has unsupported additionalData keys: "
            + ", ".join(sorted(str(value) for value in unsupported_children))
        )
    for child_key, child in additional.items():
        _validate_final_descriptor(str(child_key), child, allow_additional=False)


def _content_lives_in_additional_data(
    descriptor_key: str,
    descriptor: dict[str, Any],
    allow_additional: bool,
) -> bool:
    """Whether this descriptor legitimately has no ``data`` because a child carries the content.

    Only ever true for a primary descriptor (``allow_additional``), for the one key in
    :data:`_FINAL_CONTENT_IN_ADDITIONAL_DATA`, and only when the child actually holds non-empty
    ``data`` -- so a descriptor that is simply empty is still rejected, and ``data`` remains
    mandatory everywhere else, including on the child itself.
    """
    if not allow_additional or "data" in descriptor:
        return False
    child_key = _FINAL_CONTENT_IN_ADDITIONAL_DATA.get(descriptor_key)
    if child_key is None:
        return False
    additional = descriptor.get("additionalData")
    if not isinstance(additional, dict):
        return False
    child = additional.get(child_key)
    if not isinstance(child, dict):
        return False
    child_data = child.get("data")
    return isinstance(child_data, str) and bool(child_data)


def _validate_final_source(source: Any, descriptor_key: str) -> None:
    """Require a target source to be exactly ``{"fromUrl": {"url": ...}}`` (URL-only in the new version)."""
    if not isinstance(source, dict) or set(source) != {"fromUrl"}:
        raise RegistryApiError(f"Target descriptor {descriptor_key!r} source must contain exactly one fromUrl object")
    from_url = source.get("fromUrl")
    if not isinstance(from_url, dict) or not isinstance(from_url.get("url"), str) or not from_url["url"]:
        raise RegistryApiError(f"Target descriptor {descriptor_key!r} source.fromUrl.url is required")
    unsupported = set(from_url) - {"url", "credentialProviderConfigurations"}
    if unsupported:
        raise RegistryApiError(
            f"Target descriptor {descriptor_key!r} source.fromUrl has unsupported fields: "
            + ", ".join(sorted(str(value) for value in unsupported))
        )
    credentials = from_url.get("credentialProviderConfigurations")
    if credentials is not None and not isinstance(credentials, list):
        raise RegistryApiError(
            f"Target descriptor {descriptor_key!r} credentialProviderConfigurations must be an array"
        )


def _build_update_body(
    record: dict[str, Any],
    current_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a target UpdateRegistryRecord body of ``optionalValue`` wrappers from desired vs current.

    Fields present in the desired record are set via ``{"optionalValue": ...}``; fields present
    only in the current record are cleared via ``{}``. ``triggerSynchronization`` is added when
    the desired descriptors carry a source.
    """
    current = current_record if isinstance(current_record, dict) else {}
    desired = _build_create_body(record)
    body: dict[str, Any] = {
        "name": copy.deepcopy(desired["name"]),
        "recordType": copy.deepcopy(desired["recordType"]),
        "descriptors": {
            "optionalValue": _wrap_final_descriptors(
                desired["descriptors"],
                current.get("descriptors"),
            )
        },
    }
    for field_name in ("displayName", "description"):
        if field_name in desired:
            body[field_name] = {"optionalValue": copy.deepcopy(desired[field_name])}
        elif field_name in current:
            body[field_name] = {}
    if "recordVersion" in desired:
        body["recordVersion"] = copy.deepcopy(desired["recordVersion"])
    if _contains_descriptor_source(desired["descriptors"]):
        body["triggerSynchronization"] = True
    return body


def _wrap_final_descriptors(desired: Any, current: Any) -> dict[str, Any]:
    """Wrap the single primary descriptor for update and clear any other current primaries."""
    if not isinstance(desired, dict) or len(desired) != 1:
        raise RegistryApiError("Target update requires exactly one primary descriptor")
    current_descriptors = current if isinstance(current, dict) else {}
    primary_key, descriptor = next(iter(desired.items()))
    current_descriptor = current_descriptors.get(primary_key)
    wrapped: dict[str, Any] = {
        primary_key: {
            "optionalValue": _wrap_final_descriptor(
                descriptor,
                current_descriptor if isinstance(current_descriptor, dict) else {},
            )
        }
    }
    for current_key, current_value in current_descriptors.items():
        if current_key != primary_key and current_value is not None:
            wrapped[current_key] = {}
    return wrapped


def _wrap_final_descriptor(desired: Any, current: Any) -> dict[str, Any]:
    """Recursively wrap one descriptor's fields and additionalData children for update."""
    if not isinstance(desired, dict):
        raise RegistryApiError("Target update descriptor must be an object")
    current_value = current if isinstance(current, dict) else {}
    wrapped: dict[str, Any] = {}
    for field_name in ("data", "dataSchemaVersion", "source"):
        if field_name in desired:
            wrapped[field_name] = {"optionalValue": copy.deepcopy(desired[field_name])}
        elif field_name in current_value:
            wrapped[field_name] = {}
    desired_additional = desired.get("additionalData")
    current_additional = current_value.get("additionalData")
    if isinstance(desired_additional, dict):
        children: dict[str, Any] = {}
        current_children = current_additional if isinstance(current_additional, dict) else {}
        for child_key, child in desired_additional.items():
            children[child_key] = {
                "optionalValue": _wrap_final_descriptor(
                    child,
                    current_children.get(child_key),
                )
            }
        for child_key, child in current_children.items():
            if child_key not in desired_additional and child is not None:
                children[child_key] = {}
        wrapped["additionalData"] = {"optionalValue": children}
    elif isinstance(current_additional, dict) and current_additional:
        wrapped["additionalData"] = {}
    return wrapped


def _contains_descriptor_source(value: Any) -> bool:
    """Return True if ``value`` or any nested descriptor dict carries a ``source``."""
    if not isinstance(value, dict):
        return False
    if "source" in value:
        return True
    return any(_contains_descriptor_source(child) for child in value.values() if isinstance(child, dict))


def _record_matches_desired(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Return True if a live record already equals the desired one (skip a redundant update).

    Two different questions live here. *Which* record is this (identity) tolerates the fields the
    service owns; *is it already what we want* (equality) must not. For a synchronized record the
    service rewrites the name and the descriptor content from the fetched document, so comparing
    those would mean updating forever -- but the description is ours, so a change to it still has to
    be written through.
    """
    if _source_backed_record_matches(actual, desired):
        return actual.get("description") == desired.get("description")
    expected = _build_create_body(desired)
    for field_name in ("name", "displayName", "recordType", "description"):
        if actual.get(field_name) != expected.get(field_name):
            return False
    if not _descriptors_match(actual.get("descriptors"), expected.get("descriptors")):
        return False
    return _versions_equal(actual.get("recordVersion"), expected.get("recordVersion"))


def _source_backed_record_matches(
    actual: dict[str, Any],
    desired: dict[str, Any],
) -> bool:
    """Match a source-backed record on sources + recordType + recordVersion, ignoring name."""
    desired_sources = _descriptor_sources(desired.get("descriptors"))
    if not desired_sources:
        return False
    if _descriptor_sources(actual.get("descriptors")) != desired_sources:
        return False
    if actual.get("recordType") != desired.get("recordType"):
        return False
    # `description` is not part of the identity -- see _source_identity for why.
    return _versions_equal(actual.get("recordVersion"), desired.get("recordVersion"))


def _source_identity(record: dict[str, Any]) -> str | None:
    """Return a hashable identity for a source-backed record, or ``None`` when it has no source.

    Computed identically for a record the tool is about to write and for one the service describes,
    which is what lets a single indexing pass replace the per-record scan. The fields are exactly
    those :func:`_source_backed_record_matches` compares, so the index cannot match anything the
    pairwise comparison would have rejected.
    """
    sources = _descriptor_sources(record.get("descriptors"))
    if not sources:
        return None
    # Deliberately excludes `description`: it is mutable metadata, so including it meant that editing
    # a description in the source registry made this fallback miss and create a duplicate. Sources +
    # recordType + recordVersion identify the record; the description is then updated like any other
    # changed field.
    payload = {
        "sources": sources,
        "recordType": record.get("recordType"),
        "recordVersion": _normalized_version(record.get("recordVersion")),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _descriptor_sources(descriptors: Any) -> dict[str, Any]:
    """Return a map of descriptor path -> source for the primary and its additionalData."""
    if not isinstance(descriptors, dict) or len(descriptors) != 1:
        return {}
    primary_key, primary = next(iter(descriptors.items()))
    if not isinstance(primary, dict):
        return {}
    sources: dict[str, Any] = {}

    def collect(path: str, descriptor: Any) -> None:
        if not isinstance(descriptor, dict):
            return
        if "source" in descriptor:
            sources[path] = copy.deepcopy(descriptor["source"])
        additional = descriptor.get("additionalData")
        if not isinstance(additional, dict):
            return
        for child_key, child in additional.items():
            collect(f"{path}.additionalData.{child_key}", child)

    collect(str(primary_key), primary)
    return sources


def _descriptors_match(actual: Any, desired: Any) -> bool:
    """Return True if both sides have the same single primary descriptor and it matches."""
    if not isinstance(actual, dict) or not isinstance(desired, dict):
        return False
    actual_primaries = {str(key): value for key, value in actual.items() if value is not None}
    desired_primaries = {str(key): value for key, value in desired.items() if value is not None}
    if len(actual_primaries) != 1 or len(desired_primaries) != 1:
        return False
    desired_key, desired_descriptor = next(iter(desired_primaries.items()))
    if set(actual_primaries) != {desired_key}:
        return False
    return _descriptor_matches(
        desired_key,
        actual_primaries[desired_key],
        desired_descriptor,
    )


def _descriptor_matches(descriptor_key: str, actual: Any, desired: Any) -> bool:
    """Compare data, source, version, and additionalData children of two descriptors."""
    if not isinstance(actual, dict) or not isinstance(desired, dict):
        return False
    for field_name in ("data", "source"):
        if actual.get(field_name) != desired.get(field_name):
            return False
    if not _descriptor_version_matches(
        descriptor_key,
        actual.get("dataSchemaVersion"),
        desired.get("dataSchemaVersion"),
    ):
        return False

    actual_additional = actual.get("additionalData")
    desired_additional = desired.get("additionalData")
    if desired_additional is None:
        return actual_additional in (None, {})
    if not isinstance(actual_additional, dict) or not isinstance(desired_additional, dict):
        return False
    actual_children = {str(key): value for key, value in actual_additional.items() if value is not None}
    desired_children = {str(key): value for key, value in desired_additional.items() if value is not None}
    if set(actual_children) != set(desired_children):
        return False
    return all(
        _descriptor_matches(child_key, actual_children[child_key], desired_child)
        for child_key, desired_child in desired_children.items()
    )


def _descriptor_version_matches(descriptor_key: str, actual: Any, desired: Any) -> bool:
    """Treat a service-derived version as compatible with an unspecified desired version."""
    if desired in (None, ""):
        # MCP and A2A schema versions can be derived by the service when the caller omits
        # them. A server-populated version is therefore compatible with an unspecified
        # migration value, while all other descriptor fields remain exact.
        return True
    if actual == desired:
        return True
    if descriptor_key != "a2aAgentCard" or actual in (None, ""):
        return False
    return _normalize_a2a_schema_version(actual) == _normalize_a2a_schema_version(desired)


def _normalize_a2a_schema_version(value: Any) -> str:
    """Drop trailing ``.0`` segments so e.g. ``0.2.0`` and ``0.2`` compare equal."""
    parts = str(value).strip().split(".")
    while len(parts) > 2 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def target_endpoint_url(config: dict[str, Any], region: str) -> str:
    """Resolve and pin the target endpoint to the fixed regional agent-registry-control host."""
    if _optional_string(config.get("endpointUrl")) is not None:
        raise RegistryApiError("Target endpointUrl overrides are not supported; the regional api.aws endpoint is fixed")
    template = _required_string(config, "endpointUrlTemplate", "target API")
    endpoint = template.replace("{region}", region)
    parsed = urlparse(endpoint)
    expected_host = f"agent-registry-control.{region}.api.aws"
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname != expected_host
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise RegistryApiError(
            f"Target endpointUrlTemplate must resolve to https://{expected_host} for region {region}"
        )
    return endpoint.rstrip("/")


def _record_id_from_arn(value: Any) -> str | None:
    """Extract the record id from a target record ARN, or None if it is not present."""
    if not isinstance(value, str) or "/record/" not in value:
        return None
    record_id = value.rsplit("/record/", 1)[-1]
    return record_id or None


def _client_token(registry_id: str, name: str, record_version: Any) -> str:
    """Build a deterministic idempotency token so retried creates do not duplicate records."""
    material = f"{registry_id}|{name}|{'' if record_version in (None, '') else record_version}"
    return f"migration-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _status_set(poll_config: dict[str, Any], key: str, default: list[str]) -> set[str]:
    """Read a lifecycle-status list from the poll config into a set of strings."""
    return {str(value) for value in poll_config.get(key, default)}


def _required_string(value: dict[str, Any], key: str, owner: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected.strip():
        raise RegistryApiError(f"{owner}.{key} must be configured before this API is used")
    return selected


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _dict(value: Any, owner: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryApiError(f"{owner} must be an object")
    return value


def _copy_request(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        result[key] = _copy_request(item) if isinstance(item, dict) else item
    return result


def _without_response_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "ResponseMetadata"}


def _normalized_version(value: Any) -> str | None:
    """Treat absent and empty recordVersion as the same thing."""
    return None if value in (None, "") else str(value)


def _versions_equal(left: Any, right: Any) -> bool:
    return _normalized_version(left) == _normalized_version(right)
