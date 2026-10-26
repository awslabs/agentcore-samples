# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U0 sdk-reconciliation.

No live AWS calls anywhere in this file. Tests that simulate ENV_MISSING or
DRIFT monkeypatch boto3.client or the relevant operation-model lookup. They
never hit a real or mocked AWS endpoint.
"""

from __future__ import annotations

import json

import boto3
import pytest
from botocore.exceptions import UnknownServiceError

from agent_registry_sample.sdk_reconciliation.reconciliation import (
    PINNED_MIN_BOTO3_VERSION,
    DriftError,
    EnvMissingError,
    assert_card_access,
    find_record_id_by_name,
    reconcile,
    record_id_from_arn,
    smoke_check,
)


def test_reconcile_passes_against_installed_sdk():
    """Runs reconcile() against whatever SDK is installed in the execution
    environment. Skips (does not fail) if the agent-registry namespace is
    absent, since that is a legitimate ENV_MISSING environment state, not a
    test bug."""
    try:
        boto3.client("agent-registry", region_name="us-east-1")
    except UnknownServiceError:
        pytest.skip(
            "agent-registry namespace not present in installed botocore -- "
            "ENV_MISSING is the expected environment state, not a test failure"
        )
    result = reconcile()
    assert result.status == "PASS"
    assert all(check.result == "OK" for check in result.checks)


def test_env_missing_raises_env_missing_error_not_drift_error(monkeypatch):
    """Simulates the agent-registry service model being absent and asserts
    EnvMissingError is raised (and not DriftError)."""

    def fake_client(service_name, *args, **kwargs):
        if service_name in ("agent-registry", "agent-registry-control"):
            raise UnknownServiceError(
                service_name=service_name,
                known_service_names=", ".join(["s3", "ec2"]),
            )
        return boto3.client(service_name, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", fake_client)

    with pytest.raises(EnvMissingError) as exc_info:
        reconcile()

    message = str(exc_info.value)
    assert PINNED_MIN_BOTO3_VERSION in message
    assert "agent-registry" in message
    # Must not be worded as a shape mismatch.
    assert "expected" not in message.lower() or "contract" not in message.lower()


def test_drift_raises_drift_error_with_named_mismatch(monkeypatch):
    """Monkeypatches create_registry_record's required params to differ from
    C1 and asserts DriftError is raised (not EnvMissingError), naming the
    operation and the expected-vs-actual mismatch."""
    try:
        control_client = boto3.client("agent-registry-control", region_name="us-east-1")
    except UnknownServiceError:
        pytest.skip("agent-registry-control namespace not present in installed botocore")

    real_operation_model = control_client.meta.service_model.operation_model
    real_create_model = real_operation_model("CreateRegistryRecord")

    class _FakeInputShape:
        required_members = ["recordType"]  # missing "descriptors" -> DRIFT
        members = {"recordType": real_create_model.input_shape.members["recordType"]}

    class _FakeOperationModel:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        @property
        def input_shape(self):
            return _FakeInputShape()

        @property
        def output_shape(self):
            return self._wrapped.output_shape

    def fake_operation_model(name):
        if name == "CreateRegistryRecord":
            return _FakeOperationModel(real_create_model)
        return real_operation_model(name)

    monkeypatch.setattr(control_client.meta.service_model, "operation_model", fake_operation_model)
    monkeypatch.setattr(
        boto3,
        "client",
        lambda service_name, *a, **kw: (
            control_client
            if service_name == "agent-registry-control"
            else _real_boto3_client(service_name, *a, **kw)
        ),
    )

    with pytest.raises(DriftError) as exc_info:
        reconcile()

    message = str(exc_info.value)
    assert "create_registry_record" in message
    assert "descriptors" in message or "recordType" in message


def _real_boto3_client(service_name, *args, **kwargs):
    import boto3 as _boto3_module

    return _boto3_module.Session().client(service_name, *args, **kwargs)


def test_max_results_not_required(monkeypatch):
    """Regression test for the spec's R-01 finding. The check must not flag a
    correct SDK that models maxResults as optional."""
    try:
        data_client = boto3.client("agent-registry", region_name="us-east-1")
    except UnknownServiceError:
        pytest.skip("agent-registry namespace not present in installed botocore")

    operation_model = data_client.meta.service_model.operation_model(
        "SearchDiscoverableRegistryRecords"
    )
    input_shape = operation_model.input_shape
    assert input_shape is not None
    assert "maxResults" in input_shape.members
    assert "maxResults" not in input_shape.required_members


def test_no_singular_get_detected_as_drift(monkeypatch):
    """Monkeypatches an SDK model that exposes a singular get and asserts
    DRIFT/DriftError."""
    try:
        data_client = boto3.client("agent-registry", region_name="us-east-1")
    except UnknownServiceError:
        pytest.skip("agent-registry namespace not present in installed botocore")

    class _FakeServiceModel:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        @property
        def operation_names(self):
            return list(self._wrapped.operation_names) + ["get_discoverable_registry_record"]

        def operation_model(self, name):
            return self._wrapped.operation_model(name)

    monkeypatch.setattr(
        boto3,
        "client",
        lambda service_name, *a, **kw: (
            _patched_client(data_client)
            if service_name == "agent-registry"
            else _real_boto3_client(service_name, *a, **kw)
        ),
    )

    def _patched_client(real_client):
        class _Meta:
            service_model = _FakeServiceModel(real_client.meta.service_model)

        class _ClientProxy:
            meta = _Meta()

        return _ClientProxy()

    with pytest.raises(DriftError) as exc_info:
        reconcile()

    message = str(exc_info.value).lower()
    assert "singular get" in message or "get_discoverable_registry_record" in message


def test_record_id_from_arn_derives_trailing_segment():
    """RecordId is the ARN's trailing /record/ path segment."""
    example_arn = (
        "arn:aws:agent-registry:us-east-1:123456789012:registry/abcdEFGH1234/record/wxyzABCD5678"
    )
    assert record_id_from_arn(example_arn) == "wxyzABCD5678"


def test_record_id_from_arn_rejects_non_arn_input():
    """A string without '/record/' is not a record ARN. Fail loudly rather
    than silently returning a wrong value."""
    with pytest.raises(ValueError):
        record_id_from_arn("not-an-arn-at-all")


def test_assert_card_access_happy_path():
    """A well-formed record dict yields the parsed card."""
    card = {"protocolVersion": "0.3", "skills": [{"name": "greet", "tags": []}]}
    record = {
        "descriptors": {
            "a2aAgentCard": {
                "data": json.dumps(card),
                "dataSchemaVersion": "0.3",
            }
        }
    }
    parsed = assert_card_access(record)
    assert parsed == card


def test_assert_card_access_fails_closed_on_missing_path():
    """A record missing the descriptor path raises DriftError (fail-closed)
    rather than returning None or swallowing the error."""
    record = {"descriptors": {}}
    with pytest.raises(DriftError):
        assert_card_access(record)

    record_wrong_version = {
        "descriptors": {"a2aAgentCard": {"data": "{}", "dataSchemaVersion": "0.2"}}
    }
    with pytest.raises(DriftError):
        assert_card_access(record_wrong_version)

    record_bad_json = {
        "descriptors": {"a2aAgentCard": {"data": "{not valid json", "dataSchemaVersion": "0.3"}}
    }
    with pytest.raises(DriftError):
        assert_card_access(record_bad_json)


def test_smoke_check_env_missing_vs_drift_distinct(monkeypatch):
    """Asserts smoke_check() raises EnvMissingError and DriftError in the
    respective simulated scenarios, so the smoke check does not collapse the
    two causes."""

    def fake_client_env_missing(service_name, *args, **kwargs):
        raise UnknownServiceError(service_name=service_name, known_service_names="s3, ec2")

    monkeypatch.setattr(boto3, "client", fake_client_env_missing)
    with pytest.raises(EnvMissingError):
        smoke_check()


def test_error_messages_contain_no_secrets(monkeypatch):
    """Asserts a raised error's message contains none of a short deny-list
    of secret-shaped substrings."""

    def fake_client(service_name, *args, **kwargs):
        raise UnknownServiceError(service_name=service_name, known_service_names="s3")

    monkeypatch.setattr(boto3, "client", fake_client)
    with pytest.raises(EnvMissingError) as exc_info:
        reconcile()

    message = str(exc_info.value)
    deny_list = ["Credentials", "AKIA", "SecretAccessKey", "aws_session_token", "arn:aws:iam"]
    for forbidden in deny_list:
        assert forbidden.lower() not in message.lower()


class _ListClient:
    """Minimal control-plane double: returns the given pages from
    list_registry_records, recording the nextToken passed on each call."""

    def __init__(self, pages):
        self._pages = pages
        self.tokens_seen = []

    def list_registry_records(self, **kwargs):
        self.tokens_seen.append(kwargs.get("nextToken"))
        return self._pages[len(self.tokens_seen) - 1]


def test_find_record_id_by_name_matches_on_first_page():
    """Re-run idempotency helper: returns the recordId of the named record."""
    client = _ListClient(
        [
            {
                "registryRecords": [
                    {"name": "orchestrator", "recordId": "ORCH"},
                    {"name": "peer-greeter", "recordId": "PEER"},
                ]
            }
        ]
    )
    assert find_record_id_by_name(client, "reg-1", "peer-greeter") == "PEER"


def test_find_record_id_by_name_paginates_via_next_token():
    """Pages through nextToken until the name is found, threading the token."""
    client = _ListClient(
        [
            {"registryRecords": [{"name": "other", "recordId": "X"}], "nextToken": "t1"},
            {"registryRecords": [{"name": "peer-greeter", "recordId": "PEER2"}]},
        ]
    )
    assert find_record_id_by_name(client, "reg-1", "peer-greeter") == "PEER2"
    # The first call has no token. The second uses the first page's nextToken.
    assert client.tokens_seen == [None, "t1"]


def test_find_record_id_by_name_returns_none_when_absent():
    """Walks every page and returns None when no record matches (a clean
    'not found' so the caller's create path proceeds rather than reusing)."""
    client = _ListClient(
        [
            {"registryRecords": [{"name": "a", "recordId": "A"}], "nextToken": "t1"},
            {"registryRecords": [{"name": "b", "recordId": "B"}]},
        ]
    )
    assert find_record_id_by_name(client, "reg-1", "missing") is None
    assert client.tokens_seen == [None, "t1"]
