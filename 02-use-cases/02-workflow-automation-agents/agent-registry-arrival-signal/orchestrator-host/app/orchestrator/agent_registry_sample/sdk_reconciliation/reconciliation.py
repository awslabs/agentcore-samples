# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U0 sdk-reconciliation. A build-time check that the installed botocore models
expose the Agent Registry operations and shapes this sample relies on (the
pinned contract, "C1").

This module makes no live AWS calls and resolves no credentials. It
introspects the installed botocore operation models only.

Two distinct failure modes:
  - ENV_MISSING: the ``agent-registry`` / ``agent-registry-control`` service
    models are not present in the installed botocore at all (for example, boto3
    below the pinned minimum 1.43.116). This is a prerequisite condition, not a
    contract mismatch.
  - DRIFT (BLOCKED): the namespace is present, but an operation or shape differs
    from pinned contract C1.

Error messages are limited to an allowlist of fields (client, operation or shape
name, expected value, actual value or "absent", boto3 version string). They never
include credentials, host identifiers, or environment variables.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

import boto3
from botocore.client import Config
from botocore.exceptions import UnknownServiceError

# Pinned minimum per constraint C-01. boto3 1.43.7 ships only the legacy
# `bedrock-agentcore` namespace. 1.43.116 adds the `agent-registry` /
# `agent-registry-control` clients.
PINNED_MIN_BOTO3_VERSION = "1.43.116"

CheckResult = Literal["OK", "DRIFT", "ENV_MISSING"]
ReconciliationStatus = Literal["PASS", "ENV_MISSING", "BLOCKED"]

_CONTROL_PLANE_SERVICE = "agent-registry-control"
_DATA_PLANE_SERVICE = "agent-registry"


class EnvMissingError(Exception):
    """The agent-registry / agent-registry-control namespace is not modeled
    by the installed botocore at all. This is a prerequisite failure, not a
    contract mismatch, because no shape comparison was reachable."""


class DriftError(Exception):
    """The namespace is present, but an operation or shape differs from the
    pinned contract C1. Names the exact mismatch (operation or shape, expected
    vs actual)."""


@dataclass(frozen=True)
class CheckOutcome:
    """One assertion's outcome. Fields are limited to an allowlist (check id,
    result, expected, actual) and never include credentials, host identifiers,
    or environment variables."""

    check_id: str
    result: CheckResult
    expected: str | None = None
    actual: str | None = None

    def describe(self) -> str:
        if self.result == "OK":
            return f"{self.check_id}: OK"
        return f"{self.check_id}: {self.result} (expected {self.expected}, got {self.actual})"


@dataclass
class ReconciliationResult:
    """The overall result of a reconciliation run."""

    status: ReconciliationStatus
    checks: list[CheckOutcome] = field(default_factory=list)
    boto3_version: str = boto3.__version__

    @property
    def drifted_checks(self) -> list[CheckOutcome]:
        return [c for c in self.checks if c.result == "DRIFT"]


def _build_introspection_client(service_name: str):
    """Construct a boto3 client used only to read its operation model.

    The client is credential-free and offline, with zero retries and short
    timeouts. It never calls an operation, and only ``.meta.service_model`` is
    read. Raises ``UnknownServiceError`` (via boto3) if the installed botocore
    does not model this service at all. Callers translate that into ENV_MISSING,
    not DRIFT.
    """
    config = Config(retries={"max_attempts": 0}, connect_timeout=1, read_timeout=1)
    return boto3.client(service_name, region_name="us-east-1", config=config)


def _check_both_clients_present() -> tuple[object, object]:
    """Assert botocore can construct both clients.

    Raises ``EnvMissingError`` (not worded as drift) if the agent-registry /
    agent-registry-control service models are absent from the installed
    botocore, for example when the installed boto3 is below the pinned minimum.
    Returns the two constructed clients on success.
    """
    try:
        control_client = _build_introspection_client(_CONTROL_PLANE_SERVICE)
        data_client = _build_introspection_client(_DATA_PLANE_SERVICE)
    except UnknownServiceError:
        raise EnvMissingError(
            f"Install boto3 >= {PINNED_MIN_BOTO3_VERSION} for the agent-registry namespace "
            f"(installed boto3 {boto3.__version__} does not model the "
            f"'{_CONTROL_PLANE_SERVICE}'/'{_DATA_PLANE_SERVICE}' service(s))."
        ) from None
    return control_client, data_client


def _required_members(client, operation_name: str) -> set[str]:
    """Return the required input-shape member names for an operation.

    ``operation_name`` is botocore's PascalCase API operation name (for example
    ``CreateRegistryRecord``), not boto3's snake_case method name, because
    ``client.meta.service_model.operation_model()`` only recognizes the former.
    """
    operation_model = client.meta.service_model.operation_model(operation_name)
    input_shape = operation_model.input_shape
    if input_shape is None:
        return set()
    return set(input_shape.required_members)


# Maps the snake_case boto3 client method name (used in check_id naming) to
# botocore's PascalCase API operation name, the only form
# client.meta.service_model.operation_model() accepts.
_CONTROL_PLANE_OPERATION_NAMES = {
    "create_registry_record": "CreateRegistryRecord",
    "submit_registry_record_for_approval": "SubmitRegistryRecordForApproval",
    "update_registry_record_status": "UpdateRegistryRecordStatus",
}
_DATA_PLANE_OPERATION_NAMES = {
    "search_discoverable_registry_records": "SearchDiscoverableRegistryRecords",
    "batch_get_discoverable_registry_record": "BatchGetDiscoverableRegistryRecord",
}


def _check_control_plane_ops(client) -> list[CheckOutcome]:
    """Agent-registry-control operations present with expected
    required params, via the operation model (no live calls).

    Per the live service model, create_registry_record's response exposes
    recordArn + status, not recordId. update_registry_record_status requires
    registryId, recordId, status, and statusReason (not just status).
    """
    outcomes: list[CheckOutcome] = []
    expected_required = {
        "create_registry_record": {"recordType", "descriptors"},
        "submit_registry_record_for_approval": {"registryId", "recordId"},
        "update_registry_record_status": {"registryId", "recordId", "status", "statusReason"},
    }
    for method_name, expected in expected_required.items():
        operation_name = _CONTROL_PLANE_OPERATION_NAMES[method_name]
        try:
            actual = _required_members(client, operation_name)
        except Exception:  # noqa: BLE001 - unknown operation surfaces as DRIFT
            outcomes.append(
                CheckOutcome(
                    check_id=f"R-U0-02:{method_name}",
                    result="DRIFT",
                    expected=f"operation present with required {sorted(expected)}",
                    actual="operation absent",
                )
            )
            continue
        if not expected.issubset(actual):
            outcomes.append(
                CheckOutcome(
                    check_id=f"R-U0-02:{method_name}",
                    result="DRIFT",
                    expected=sorted(expected),
                    actual=sorted(actual),
                )
            )
        else:
            outcomes.append(CheckOutcome(check_id=f"R-U0-02:{method_name}", result="OK"))

    # create_registry_record's output must expose recordArn + status
    # (recordId is not returned directly).
    try:
        output_shape = client.meta.service_model.operation_model(
            _CONTROL_PLANE_OPERATION_NAMES["create_registry_record"]
        ).output_shape
        output_members = set(output_shape.members) if output_shape is not None else set()
        expected_output = {"recordArn", "status"}
        if expected_output.issubset(output_members):
            outcomes.append(
                CheckOutcome(check_id="R-U0-02:create_registry_record.output", result="OK")
            )
        else:
            outcomes.append(
                CheckOutcome(
                    check_id="R-U0-02:create_registry_record.output",
                    result="DRIFT",
                    expected=sorted(expected_output),
                    actual=sorted(output_members),
                )
            )
    except Exception:  # noqa: BLE001
        outcomes.append(
            CheckOutcome(
                check_id="R-U0-02:create_registry_record.output",
                result="DRIFT",
                expected=sorted({"recordArn", "status"}),
                actual="operation absent",
            )
        )
    return outcomes


def record_id_from_arn(record_arn: str) -> str:
    """Derive recordId from recordArn's trailing path segment.

    In the live service model, recordArn's output pattern is
    ``arn:aws(-[^:]+)?:agent-registry:[a-z0-9-]+:[0-9]{12}:registry/
    [a-zA-Z0-9]{12,16}/record/[a-zA-Z0-9]{12}``. recordId's accepted input
    pattern on submit_registry_record_for_approval and
    update_registry_record_status is ``(<that same ARN prefix>)?[a-zA-Z0-9]{12}``,
    so recordId is the ARN's trailing 12-character segment.

    Both downstream operations also accept the full recordArn in place of the
    bare id, so a caller that prefers to forward recordArn as-is can skip this
    function.

    Raises ValueError if ``record_arn`` does not contain the expected
    ``/record/`` separator (that is, it does not look like a record ARN at all).
    """
    if "/record/" not in record_arn:
        raise ValueError(
            f"'{record_arn}' does not look like a registry record ARN "
            "(missing '/record/' path segment)"
        )
    return record_arn.rsplit("/record/", 1)[-1]


def _check_data_plane_ops(client) -> list[CheckOutcome]:
    """Agent-registry operations present with expected params.

    maxResults is asserted optional and accepted (range 1-20 documented), not
    required. Asserting it as required would false-positive against a correct
    SDK that models maxResults as optional.
    """
    outcomes: list[CheckOutcome] = []

    # search_discoverable_registry_records: registryIds + searchQuery required.
    # maxResults must be accepted (a member of the input shape) but not required.
    try:
        operation_model = client.meta.service_model.operation_model(
            _DATA_PLANE_OPERATION_NAMES["search_discoverable_registry_records"]
        )
        input_shape = operation_model.input_shape
        all_members = set(input_shape.members) if input_shape is not None else set()
        required = set(input_shape.required_members) if input_shape is not None else set()
        expected_required = {"registryIds", "searchQuery"}
        max_results_accepted = "maxResults" in all_members
        max_results_not_required = "maxResults" not in required
        if (
            expected_required.issubset(required)
            and max_results_accepted
            and max_results_not_required
        ):
            outcomes.append(
                CheckOutcome(check_id="R-U0-03:search_discoverable_registry_records", result="OK")
            )
        else:
            outcomes.append(
                CheckOutcome(
                    check_id="R-U0-03:search_discoverable_registry_records",
                    result="DRIFT",
                    expected=(
                        "required={registryIds, searchQuery}, "
                        "maxResults accepted and optional (range 1-20)"
                    ),
                    actual=f"required={sorted(required)}, members={sorted(all_members)}",
                )
            )
    except Exception:  # noqa: BLE001
        outcomes.append(
            CheckOutcome(
                check_id="R-U0-03:search_discoverable_registry_records",
                result="DRIFT",
                expected="operation present",
                actual="operation absent",
            )
        )

    # batch_get_discoverable_registry_record: entries required.
    try:
        required = _required_members(
            client, _DATA_PLANE_OPERATION_NAMES["batch_get_discoverable_registry_record"]
        )
        if "entries" in required:
            outcomes.append(
                CheckOutcome(check_id="R-U0-03:batch_get_discoverable_registry_record", result="OK")
            )
        else:
            outcomes.append(
                CheckOutcome(
                    check_id="R-U0-03:batch_get_discoverable_registry_record",
                    result="DRIFT",
                    expected={"entries"},
                    actual=sorted(required),
                )
            )
    except Exception:  # noqa: BLE001
        outcomes.append(
            CheckOutcome(
                check_id="R-U0-03:batch_get_discoverable_registry_record",
                result="DRIFT",
                expected="operation present",
                actual="operation absent",
            )
        )
    return outcomes


def _check_no_singular_get(client) -> CheckOutcome:
    """Assert no singular get operation exists on agent-registry
    (the data-plane client). The contract uses batch-get only. A "List..."
    operation (for example ListDiscoverableRegistryRecords) is a distinct,
    documented operation and not a singular get. Only a "Get..." name
    (excluding "BatchGet...") is a drift candidate here.
    """
    operation_names = set(client.meta.service_model.operation_names)
    singular_get_candidates = {
        name
        for name in operation_names
        if name.lower().startswith("get") and "batch" not in name.lower()
    }
    if singular_get_candidates:
        return CheckOutcome(
            check_id="R-U0-04",
            result="DRIFT",
            expected="no singular get operation",
            actual=sorted(singular_get_candidates),
        )
    return CheckOutcome(check_id="R-U0-04", result="OK")


def _check_descriptor_and_output_shape(client) -> CheckOutcome:
    """Build-time introspectable descriptor + output shape facts.

    Asserts the create_registry_record request models descriptors ->
    a2aAgentCard -> (data, dataSchemaVersion). The control-plane operations
    check covers the output shape (recordArn and status, with no recordId).
    """
    try:
        operation_model = client.meta.service_model.operation_model(
            _CONTROL_PLANE_OPERATION_NAMES["create_registry_record"]
        )
        input_shape = operation_model.input_shape
        if input_shape is None or "descriptors" not in input_shape.members:
            return CheckOutcome(
                check_id="R-U0-05a",
                result="DRIFT",
                expected="descriptors member present",
                actual="descriptors member absent",
            )
        descriptors_shape = input_shape.members["descriptors"]
        descriptors_members = getattr(descriptors_shape, "members", {})
        if "a2aAgentCard" not in descriptors_members:
            return CheckOutcome(
                check_id="R-U0-05a",
                result="DRIFT",
                expected="descriptors.a2aAgentCard present",
                actual=f"descriptors members={sorted(descriptors_members)}",
            )
        card_shape = descriptors_members["a2aAgentCard"]
        card_members = set(getattr(card_shape, "members", {}))
        expected_card_members = {"data", "dataSchemaVersion"}
        if not expected_card_members.issubset(card_members):
            return CheckOutcome(
                check_id="R-U0-05a",
                result="DRIFT",
                expected=sorted(expected_card_members),
                actual=sorted(card_members),
            )
        return CheckOutcome(check_id="R-U0-05a", result="OK")
    except Exception as exc:  # noqa: BLE001
        return CheckOutcome(
            check_id="R-U0-05a",
            result="DRIFT",
            expected="create_registry_record descriptor shape present",
            actual=f"introspection failed: {type(exc).__name__}",
        )


def _check_boto3_minimum() -> CheckOutcome:
    """Record the installed boto3 version and assert >= pinned minimum.

    This is a convenience cross-check. Usually a boto3 below the pinned minimum
    is already caught as ENV_MISSING because the clients fail to construct.
    This check fires as DRIFT only when the namespace is present but the
    reported version string is still below the pinned minimum.
    """
    installed = tuple(int(part) for part in boto3.__version__.split(".")[:3])
    minimum = tuple(int(part) for part in PINNED_MIN_BOTO3_VERSION.split(".")[:3])
    if installed >= minimum:
        return CheckOutcome(check_id="R-U0-06", result="OK", actual=boto3.__version__)
    return CheckOutcome(
        check_id="R-U0-06",
        result="DRIFT",
        expected=f">= {PINNED_MIN_BOTO3_VERSION}",
        actual=boto3.__version__,
    )


def reconcile() -> ReconciliationResult:
    """Run the full build-time reconciliation.

    Construct both clients first. An EnvMissingError there short-circuits with
    status=ENV_MISSING and re-raises. Otherwise run every check, collecting
    CheckOutcomes. Any DRIFT raises DriftError naming the exact mismatch(es),
    and the result carries status=BLOCKED. All-OK returns status=PASS.
    """
    try:
        control_client, data_client = _check_both_clients_present()
    except EnvMissingError:
        raise

    checks: list[CheckOutcome] = []
    checks.extend(_check_control_plane_ops(control_client))
    checks.extend(_check_data_plane_ops(data_client))
    checks.append(_check_no_singular_get(data_client))
    checks.append(_check_descriptor_and_output_shape(control_client))
    checks.append(_check_boto3_minimum())

    drifted = [c for c in checks if c.result == "DRIFT"]
    if drifted:
        mismatch_summary = "; ".join(c.describe() for c in drifted)
        result = ReconciliationResult(status="BLOCKED", checks=checks)
        raise DriftError(
            f"SDK reconciliation found {len(drifted)} drift(s) against contract C1: "
            f"{mismatch_summary}"
        ) from _DriftResultCarrier(result)

    return ReconciliationResult(status="PASS", checks=checks)


class _DriftResultCarrier(Exception):
    """Internal: carries the ReconciliationResult as the __cause__ of a
    DriftError so callers that catch DriftError can still inspect
    result.checks via `err.__cause__.result` if needed. Not part of the
    public API."""

    def __init__(self, result: ReconciliationResult) -> None:
        super().__init__()
        self.result = result


def smoke_check() -> ReconciliationResult:
    """Introspection-only smoke check.

    Re-runs only the introspectable subset (05a, 06), with no live AWS calls.
    It reuses reconcile()'s build-time path, since reconcile() never makes live
    calls either. The only non-introspectable check is a separate function and
    is never called here.

    Re-raises EnvMissingError / DriftError distinctly so a caller (or a pytest
    smoke test) can assert on exception type, not just message text. The smoke
    check must never imply a contract mismatch when the actual cause is a
    missing or outdated SDK namespace.
    """
    return reconcile()


def assert_card_access(record: dict) -> dict:
    """Fail-closed runtime assertion on a fetched record.

    Given a registry record dict (for example from
    batch_get_discoverable_registry_record), assert the card JSON string is
    present at record["descriptors"]["a2aAgentCard"]["data"] with
    dataSchemaVersion "0.3", and that json.loads parses it. Raises DriftError
    (fail closed, so the caller must not open the A2A session) if the path is
    absent, the schema version differs, or the JSON fails to parse.

    U5 and U6 call this before opening an A2A session. It does not implement
    the A2A session itself.

    Returns the parsed agent card dict on success.
    """
    descriptors = record.get("descriptors") if isinstance(record, dict) else None
    card_descriptor = descriptors.get("a2aAgentCard") if isinstance(descriptors, dict) else None
    if not isinstance(card_descriptor, dict):
        raise DriftError(
            'card access failed closed: record["descriptors"]["a2aAgentCard"] is absent'
        )

    schema_version = card_descriptor.get("dataSchemaVersion")
    if schema_version != "0.3":
        raise DriftError(
            f"card access failed closed: expected dataSchemaVersion '0.3', got {schema_version!r}"
        )

    card_data = card_descriptor.get("data")
    if not isinstance(card_data, str):
        raise DriftError(
            "card access failed closed: "
            'record["descriptors"]["a2aAgentCard"]["data"] is absent or not a string'
        )

    try:
        parsed = json.loads(card_data)
    except json.JSONDecodeError as exc:
        raise DriftError(
            f"card access failed closed: a2aAgentCard.data did not parse as JSON ({exc})"
        ) from exc

    return parsed


def find_record_id_by_name(control_client, registry_id: str, name: str) -> str | None:
    """Return the recordId of the registry record named `name`, or None.

    Supports idempotent re-runs. The control plane has no get-by-name
    operation. Its ops are Create/Get/List/Update/Delete, and GetRegistryRecord
    takes recordId, not name. ListRegistryRecords carries `name` + `recordId` on
    each `registryRecords[]` entry, so this pages the list and matches the name
    client-side. It does not use the server-side `filters` argument because
    its accepted filter names are unconfirmed against the live model, and a
    wrong filter name would silently return nothing (a false "not found" that
    would mask an existing record and defeat the guard).

    Record names are unique within a registry (a duplicate name raises the
    ConflictException "a record with name X already exists" that this lookup
    resolves), so the first match is the record.

    The client is injected so this stays a pure, unit-testable primitive like
    record_id_from_arn above.
    """
    next_token = None
    while True:
        kwargs = {"registryId": registry_id}
        if next_token:
            kwargs["nextToken"] = next_token
        response = control_client.list_registry_records(**kwargs)
        for record in response.get("registryRecords", []):
            if record.get("name") == name:
                return record.get("recordId")
        next_token = response.get("nextToken")
        if not next_token:
            return None
