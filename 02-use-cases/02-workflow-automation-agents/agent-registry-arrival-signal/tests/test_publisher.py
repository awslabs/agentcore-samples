# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U4 publisher-approval. Covers the create -> submit -> approve
lifecycle, the two-role separation of duties, the fail-fast sequence, and the
card serialization.

No live AWS calls. The control-plane clients are fakes, and the two-role
separation is asserted at the code/session level (the submitter fake cannot
approve, and the approver fake cannot create or submit).
"""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from agent_registry_sample.publisher.publish_and_approve import (
    approve,
    create_and_submit,
    publish_and_approve,
)

_REGISTRY_ID = "abcdEFGH1234"
_RECORD_ARN = (
    "arn:aws:agent-registry:us-east-1:111111111111:registry/abcdEFGH1234/record/wxyzABCD5678"
)
_RECORD_ID = "wxyzABCD5678"


def _corrected_card_json() -> str:
    """A peer card JSON with U3's skill shape (id + description)."""
    return json.dumps(
        {
            "name": "peer-greeter",
            "description": "Greets a given name over A2A.",
            "url": "https://peer.example.invalid/a2a",
            "version": "0.1.0",
            "protocolVersion": "0.3.0",
            "capabilities": {},
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "greet",
                    "name": "greet",
                    "description": "Returns 'hello {name}'.",
                    "tags": ["greet"],
                }
            ],
            "signatures": [{"protected": "x", "signature": "y"}],
        }
    )


class _FakeSubmitterClient:
    """Has only create + submit, with no update_registry_record_status (the
    submitter role cannot approve)."""

    def __init__(self):
        self.create_call = None
        self.submit_call = None

    def create_registry_record(self, **kwargs):
        self.create_call = kwargs
        # Response has {recordArn, status} and no recordId.
        return {"recordArn": _RECORD_ARN, "status": "CREATING"}

    def submit_registry_record_for_approval(self, **kwargs):
        self.submit_call = kwargs
        return {"recordArn": _RECORD_ARN, "recordId": _RECORD_ID, "status": "PENDING_APPROVAL"}


class _FakeApproverClient:
    """Has only update_registry_record_status, with no create or submit (the
    approver role cannot create or submit)."""

    def __init__(self):
        self.update_call = None

    def update_registry_record_status(self, **kwargs):
        self.update_call = kwargs
        return {"recordArn": _RECORD_ARN, "recordId": _RECORD_ID, "status": "APPROVED"}


def test_create_passes_all_four_required_inputs():
    """Create passes registryId, name, recordType, descriptors."""
    sub = _FakeSubmitterClient()
    create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    c = sub.create_call
    assert set(["registryId", "name", "recordType", "descriptors"]).issubset(c.keys())
    assert c["recordType"] == "AGENT"
    assert c["registryId"] == _REGISTRY_ID
    assert c["descriptors"]["a2aAgentCard"]["dataSchemaVersion"] == "0.3"


def test_recordid_is_derived_from_arn_not_read_from_response():
    """Create's response has no recordId. recordId is derived via
    U0's record_id_from_arn() (the ARN's trailing segment)."""
    sub = _FakeSubmitterClient()
    record_id = create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    assert record_id == _RECORD_ID  # derived from _RECORD_ARN's trailing /record/ segment


def test_submit_uses_the_derived_recordid():
    """Submit uses the derived recordId (+ registryId)."""
    sub = _FakeSubmitterClient()
    create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    assert sub.submit_call == {"registryId": _REGISTRY_ID, "recordId": _RECORD_ID}


def test_approve_passes_all_four_params_with_nonempty_statusreason():
    """Approve passes registryId, recordId, status, statusReason.
    statusReason is present and 0-255 chars, and status='APPROVED'."""
    app = _FakeApproverClient()
    reason = "Approved by the walkthrough publisher."
    approve(app, _REGISTRY_ID, _RECORD_ID, reason)
    u = app.update_call
    assert set(["registryId", "recordId", "status", "statusReason"]) == set(u.keys())
    assert u["status"] == "APPROVED"
    assert u["statusReason"] == reason
    assert 1 <= len(u["statusReason"]) <= 255


def test_approve_rejects_empty_statusreason():
    """statusReason is never omitted, and an empty one is rejected."""
    app = _FakeApproverClient()
    with pytest.raises(ValueError):
        approve(app, _REGISTRY_ID, _RECORD_ID, "")
    assert app.update_call is None  # never called with an empty reason


def test_approve_rejects_oversized_statusreason():
    """StatusReason max 255 chars."""
    app = _FakeApproverClient()
    with pytest.raises(ValueError):
        approve(app, _REGISTRY_ID, _RECORD_ID, "x" * 256)


def test_two_role_separation_is_structural():
    """The submitter session has no approve capability and the approver
    session has no create/submit capability. The separation is structural (two
    distinct sessions), not merely sequential."""
    sub = _FakeSubmitterClient()
    app = _FakeApproverClient()
    # Submitter cannot approve:
    assert not hasattr(sub, "update_registry_record_status")
    # Approver cannot create or submit:
    assert not hasattr(app, "create_registry_record")
    assert not hasattr(app, "submit_registry_record_for_approval")


def test_publish_and_approve_uses_separate_sessions(monkeypatch):
    """The full flow uses a distinct assumed-role client for each half
    (create+submit under the submitter, approve under the approver), never one
    session holding both capabilities."""
    sub = _FakeSubmitterClient()
    app = _FakeApproverClient()
    handed_out = []

    def fake_assume(role_arn, region, session_name):
        # Submitter fake for the submitter role, approver fake for the approver.
        handed_out.append((role_arn, session_name))
        return sub if "submitter" in role_arn.lower() else app

    monkeypatch.setattr(
        "agent_registry_sample.publisher.publish_and_approve._assume_role_client", fake_assume
    )
    record_id = publish_and_approve(
        registry_id=_REGISTRY_ID,
        name="peer-greeter",
        card_json=_corrected_card_json(),
        submitter_role_arn="arn:aws:iam::111111111111:role/RegistryPublisherSubmitterRole",
        approver_role_arn="arn:aws:iam::111111111111:role/RegistryPublisherApproverRole",
        status_reason="Approved by the walkthrough publisher.",
        region="us-east-1",
    )
    assert record_id == _RECORD_ID
    # Two separate assume-role sessions were taken (one per role).
    assert len(handed_out) == 2
    # Create and submit ran on the submitter session, approve on the approver.
    assert sub.create_call is not None and sub.submit_call is not None
    assert app.update_call is not None and app.update_call["status"] == "APPROVED"


def test_create_failure_propagates_uncaught():
    """Fail-fast. A create failure propagates uncaught (no try/except
    swallow, no retry)."""

    class _FailingSubmitter:
        calls = 0

        def create_registry_record(self, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated ClientError: ValidationException")

    client = _FailingSubmitter()
    with pytest.raises(RuntimeError, match="simulated ClientError"):
        create_and_submit(client, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    assert client.calls == 1  # one attempt, no retry loop


def test_serialized_card_carries_corrected_skill_shape():
    """The card serialized into descriptors carries U3's skill shape
    (id + description), not the stale name+tags shorthand."""
    sub = _FakeSubmitterClient()
    create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    data = sub.create_call["descriptors"]["a2aAgentCard"]["data"]
    card = json.loads(data)
    skill = card["skills"][0]
    assert skill["id"] == "greet"
    assert skill["description"]  # the shape requires description
    assert skill["name"] == "greet"


def _conflict(message: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConflictException", "Message": message}},
        "SubmitRegistryRecordForApproval",
    )


def test_submit_retries_through_transient_creating_then_succeeds(monkeypatch):
    """Submit is retried while the record is in the transient CREATING
    state, then succeeds once it settles. The create->submit async race is
    waited out, not failed."""
    import agent_registry_sample.publisher.publish_and_approve as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)  # no real waiting

    class _CreatingThenReady:
        def __init__(self):
            self.create_call = None
            self.submit_attempts = 0

        def create_registry_record(self, **kwargs):
            self.create_call = kwargs
            return {"recordArn": _RECORD_ARN, "status": "CREATING"}

        def submit_registry_record_for_approval(self, **kwargs):
            self.submit_attempts += 1
            # Transient CREATING for the first two attempts, then settles.
            if self.submit_attempts < 3:
                raise _conflict("Registry record cannot be modified while in CREATING state.")
            return {"recordId": _RECORD_ID, "status": "PENDING_APPROVAL"}

    sub = _CreatingThenReady()
    record_id = create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    assert record_id == _RECORD_ID
    assert sub.submit_attempts == 3  # retried through two transient conflicts


def test_submit_failfast_on_non_transient_conflict(monkeypatch):
    """Guardrail. A ConflictException that is not a transient-state one
    (for example 'already exists') is not retried. It propagates on the first
    try, preserving the publisher's fail-fast stance."""
    import agent_registry_sample.publisher.publish_and_approve as mod

    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)

    class _AlreadyExists:
        def __init__(self):
            self.submit_attempts = 0

        def create_registry_record(self, **kwargs):
            return {"recordArn": _RECORD_ARN, "status": "CREATING"}

        def submit_registry_record_for_approval(self, **kwargs):
            self.submit_attempts += 1
            raise _conflict("A record with name 'peer-greeter' already exists in registry X.")

    sub = _AlreadyExists()
    with pytest.raises(ClientError):
        create_and_submit(sub, _REGISTRY_ID, "peer-greeter", _corrected_card_json())
    assert sub.submit_attempts == 1  # no retry on a non-transient conflict
