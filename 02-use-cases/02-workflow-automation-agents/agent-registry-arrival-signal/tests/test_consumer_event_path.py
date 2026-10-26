# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U5 consumer-event-path, the on_arrival_event behavior.

Acceptance criteria covered:
  - two-phase dedupe. (a) No double-invoke for a COMPLETED
    (recordId, state). (b) No permanent skip of a (recordId, state) whose
    handoff failed (the expired PENDING claim is re-claimable).
  - cross-runtime propagation. A failure before Phase 2
    propagates uncaught (Phase 2 never reached), so the adapter raises.

Also covered are the shared verify_and_invoke helper (a card from an allowed
account invokes, a non-allowed account fails closed), the batch_get read-back
with errors/absent fail-closed, and the input-contract match to U1's handler
payload.

No live AWS. The DynamoDB table and the data-plane client are in-memory
fakes, the A2A send is injected, and the peer card is U3's actual (unsigned)
card.
"""

from __future__ import annotations

import pytest

from agent_registry_sample.orchestrator.event_path import on_arrival_event_impl
from agent_registry_sample.orchestrator.verify_and_invoke import (
    VerificationError,
    verify_and_invoke,
)
from agent_registry_sample.peer.card import build_peer_card
from agent_registry_sample.sdk_reconciliation.reconciliation import DriftError

_PEER_URL = "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A111111111111%3Aruntime%2Fpeer-xyz/invocations"
_ALLOWED_ACCOUNTS = {"111111111111"}
_RECORD_ID = "wxyzABCD5678"
_REGISTRY_ID = "abcdEFGH1234"
_PAYLOAD = {"registryRecordId": _RECORD_ID, "registryId": _REGISTRY_ID}


# ------------------------- fakes -------------------------


class _ConditionalCheckFailed(Exception):
    """Mimics botocore's ConditionalCheckFailedException by name."""


class _FakeTable:
    """An in-memory DynamoDB table honoring attribute_not_exists(recordId)."""

    def __init__(self):
        self.items: dict[tuple, dict] = {}

    def _key(self, item_or_key):
        return (item_or_key["recordId"], item_or_key["state"])

    def put_item(self, Item, ConditionExpression=None):  # noqa: N803 - boto3 kwarg name
        k = self._key(Item)
        if ConditionExpression and "attribute_not_exists(recordId)" in ConditionExpression:
            if k in self.items:
                raise _ConditionalCheckFailed("ConditionalCheckFailedException")
        self.items[k] = dict(Item)

    def get_item(self, Key):  # noqa: N803
        item = self.items.get(self._key(Key))
        return {"Item": dict(item)} if item else {}

    def update_item(
        self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues
    ):  # noqa: N803, E501
        item = self.items[self._key(Key)]
        item["status"] = ExpressionAttributeValues[":c"]
        item["expiresAt"] = ExpressionAttributeValues[":t"]


def _peer_record() -> dict:
    """A registry record whose descriptors carry U3's actual (unsigned) card."""
    card = build_peer_card(_PEER_URL)
    return {
        "recordId": _RECORD_ID,
        "status": "APPROVED",
        "descriptors": {
            "a2aAgentCard": {
                "data": card.model_dump_json(exclude_none=True, by_alias=True),
                "dataSchemaVersion": "0.3",
            }
        },
    }


class _FakeDataPlane:
    def __init__(self, record=None, errors=None):
        self._record = record
        self._errors = errors or []

    def batch_get_discoverable_registry_record(self, entries):
        return {
            "registryRecords": [self._record] if self._record else [],
            "errors": self._errors,
        }


def _ok_send(url, params):
    """A2A send double that returns the peer's expected greet response shape."""
    name = params.message.parts[0].root.text if hasattr(params.message.parts[0], "root") else None
    return {"role": "agent", "parts": [{"kind": "text", "text": f"hello {name}"}]}


# ------------------------- verify_and_invoke (shared helper) -------------------------


def test_verify_and_invoke_reuses_u3_verifier_legit_peer_passes():
    """The shared helper accepts a card from an allowed peer account and calls
    greet, returning 'hello {name}'."""
    record = _peer_record()
    result = verify_and_invoke(record, "world", _ALLOWED_ACCOUNTS, _ok_send)
    assert result == "hello world"


def test_verify_and_invoke_non_allowed_account_fails_closed():
    """A card whose runtime account is not allowed fails
    closed with no send."""
    record = _peer_record()
    sent = []
    with pytest.raises(VerificationError):
        verify_and_invoke(record, "world", {"999999999999"}, lambda u, p: sent.append(1))
    assert sent == []


def test_verify_and_invoke_missing_card_fails_closed_via_u0_primitive():
    """A record with no card descriptor fails closed via U0's
    assert_card_access (DriftError)."""
    empty_record = {"recordId": _RECORD_ID, "descriptors": {}}
    with pytest.raises(DriftError):
        verify_and_invoke(empty_record, "world", _ALLOWED_ACCOUNTS, _ok_send)


# ------------------------- on_arrival_event (event path) -------------------------


def test_input_contract_matches_u1_handler_payload():
    """On_arrival_event accepts exactly {registryRecordId,
    registryId} (U1's handler.py payload). registryRecordId is the recordId."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())
    result = on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        greet_name="world",
        now=1000,
    )
    assert result == "hello world"
    # the dedupe item is keyed on the payload's registryRecordId as recordId
    assert (_RECORD_ID, "APPROVED") in table.items


def test_happy_path_marks_completed_after_handoff():
    """Phase 2 COMPLETE runs only after the A2A handoff succeeds."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())
    on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        greet_name="world",
        now=1000,
    )
    assert table.items[(_RECORD_ID, "APPROVED")]["status"] == "COMPLETED"


def test_dedupe_guarantee_a_no_double_invoke_for_completed():
    """A redelivery of a COMPLETED (recordId, state) is an
    idempotent skip. The peer is not invoked a second time."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())
    sends = []

    def counting_send(url, params):
        sends.append(1)
        return _ok_send(url, params)

    # First delivery: full handoff -> COMPLETED.
    on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=counting_send,
        greet_name="world",
        now=1000,
    )
    assert len(sends) == 1
    # Redelivery within the COMPLETED retention must skip, with no 2nd send.
    result = on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=counting_send,
        greet_name="world",
        now=1100,
    )
    assert result is None  # idempotent skip
    assert len(sends) == 1  # no double-invoke


def test_dedupe_guarantee_b_no_permanent_skip_after_failed_handoff():
    """A (recordId, state) whose handoff failed must be
    re-processable on retry. The expired PENDING claim is re-claimable, so
    a later retry completes the handoff (not permanently skipped)."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())

    def failing_send(url, params):
        raise RuntimeError("transient A2A network error")

    # First delivery claims PENDING, then the A2A handoff fails and propagates
    # uncaught (Phase 2 never reached). The PENDING item lingers.
    with pytest.raises(RuntimeError, match="transient A2A"):
        on_arrival_event_impl(
            _PAYLOAD,
            ddb_table=table,
            data_plane_client=dp,
            allowed_accounts=_ALLOWED_ACCOUNTS,
            send_message=failing_send,
            greet_name="world",
            now=1000,
        )
    assert table.items[(_RECORD_ID, "APPROVED")]["status"] == "PENDING"  # not COMPLETED

    # Retry after the short PENDING TTL expired (now >> 1000 + 300). The claim
    # is re-claimable, and a succeeding handoff completes it.
    result = on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        greet_name="world",
        now=1000 + 10_000,
    )
    assert result == "hello world"  # not permanently skipped
    assert table.items[(_RECORD_ID, "APPROVED")]["status"] == "COMPLETED"


def test_in_flight_pending_not_expired_does_not_double_invoke():
    """A redelivery while a PENDING claim is still live (not expired) does
    not double-invoke (concurrency safety from the conditional put)."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())
    # Simulate a live PENDING claim (not expired).
    table.items[(_RECORD_ID, "APPROVED")] = {
        "recordId": _RECORD_ID,
        "state": "APPROVED",
        "status": "PENDING",
        "expiresAt": 2000,
    }
    sends = []
    result = on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=lambda u, p: sends.append(1),
        greet_name="world",
        now=1500,
    )
    assert result is None  # skipped as in-flight
    assert sends == []


def test_cross_runtime_propagation_failure_uncaught_phase2_not_reached():
    """A failure (here a verify failure) propagates uncaught and
    Phase 2 complete is not reached, so the orchestrator's invoke fails and
    U1's adapter raises -> SQS retry/DLQ. Never swallowed into a success."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=_peer_record())
    # Non-allowed account -> VerificationError (fail-closed), uncaught.
    with pytest.raises(VerificationError):
        on_arrival_event_impl(
            _PAYLOAD,
            ddb_table=table,
            data_plane_client=dp,
            allowed_accounts={"999999999999"},
            send_message=_ok_send,
            greet_name="world",
            now=1000,
        )
    # Phase 2 not reached, so the item is still PENDING, never COMPLETED.
    assert table.items[(_RECORD_ID, "APPROVED")]["status"] == "PENDING"


def test_batch_get_errors_fail_closed():
    """A record in batch_get's errors list is a fail-closed
    condition. The path raises (no peer call)."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=None, errors=[{"recordId": _RECORD_ID, "errorCode": "AccessDenied"}])
    with pytest.raises(RuntimeError, match="errors"):
        on_arrival_event_impl(
            _PAYLOAD,
            ddb_table=table,
            data_plane_client=dp,
            allowed_accounts=_ALLOWED_ACCOUNTS,
            send_message=_ok_send,
            greet_name="world",
            now=1000,
        )


def test_batch_get_absent_record_fails_closed():
    """An absent record (empty registryRecords) is a fail-closed
    condition."""
    table = _FakeTable()
    dp = _FakeDataPlane(record=None, errors=[])
    with pytest.raises(RuntimeError, match="no record"):
        on_arrival_event_impl(
            _PAYLOAD,
            ddb_table=table,
            data_plane_client=dp,
            allowed_accounts=_ALLOWED_ACCOUNTS,
            send_message=_ok_send,
            greet_name="world",
            now=1000,
        )


def test_batch_get_uses_dataplane_entries_shape():
    """Batch_get is called with entries=[{registryId,
    recordIds:[recordId]}]."""
    table = _FakeTable()
    captured = {}

    class _CapturingDP:
        def batch_get_discoverable_registry_record(self, entries):
            captured["entries"] = entries
            return {"registryRecords": [_peer_record()], "errors": []}

    on_arrival_event_impl(
        _PAYLOAD,
        ddb_table=table,
        data_plane_client=_CapturingDP(),
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        greet_name="world",
        now=1000,
    )
    assert captured["entries"] == [{"registryId": _REGISTRY_ID, "recordIds": [_RECORD_ID]}]
