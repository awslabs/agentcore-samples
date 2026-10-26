# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U2 orchestrator-host: the hello_world skill, the three tools
(on_arrival_event backed by U5, search_and_invoke backed by U6), the
OrchestratorExecutionRole's CDK-level IAM scoping, and the registration
script's recordId derivation.

No live AWS calls. The registration tests monkeypatch the control-plane client
the same way U0's tests mock boto3.client.
"""

from __future__ import annotations

import pytest

from agent_registry_sample.orchestrator.agent import (
    build_agent,
    hello_world,
    on_arrival_event,
    search_and_invoke,
)
from agent_registry_sample.orchestrator.register_orchestrator import (
    build_orchestrator_card,
    register_orchestrator,
)


def test_hello_world_returns_exact_literal_string():
    """No input, returns exactly "hello world"."""
    assert hello_world() == "hello world"


def test_on_arrival_event_is_implemented_by_u5_not_a_stub():
    """on_arrival_event is wired to the event path. Its full behavioral
    coverage lives in tests/test_consumer_event_path.py.

    With the runtime wiring unconfigured (no IDEMPOTENCY_TABLE_NAME env var),
    invoking the tool fails while building its collaborators. A missing-wiring
    KeyError, rather than a NotImplementedError, shows the event path is wired.
    """
    with pytest.raises(Exception) as exc_info:
        on_arrival_event({"registryRecordId": "abc123", "registryId": "def456"})
    assert not isinstance(exc_info.value, NotImplementedError)


def test_on_arrival_event_accepts_u1_handler_payload_shape():
    """The input contract matches U1's handler.py payload shape,
    {"registryRecordId": ..., "registryId": ...}. The signature accepts this
    shape (it fails later on missing runtime wiring, not on a signature
    TypeError). Full behavioral coverage of this payload is in
    tests/test_consumer_event_path.py
    (test_input_contract_matches_u1_handler_payload)."""
    payload = {"registryRecordId": "rec-123", "registryId": "reg-456"}
    try:
        on_arrival_event(payload)
    except TypeError as exc:  # pragma: no cover - would indicate a signature mismatch
        raise AssertionError(f"signature rejected U1's payload shape: {exc}") from exc
    except Exception:
        # Any non-TypeError (e.g. missing runtime wiring) is fine, because the
        # signature accepted the payload, which is what this test checks.
        pass


def test_search_and_invoke_is_implemented_by_u6_not_a_stub():
    """search_and_invoke is wired to the search path. Its full behavioral
    coverage lives in tests/test_search_path.py.

    With the runtime wiring unconfigured (no AGENT_REGISTRY_ID env var),
    invoking the tool fails while building its collaborators. A missing-wiring
    error, rather than a NotImplementedError, shows the search path is wired."""
    with pytest.raises(Exception) as exc_info:
        search_and_invoke("some query")
    assert not isinstance(exc_info.value, NotImplementedError)


def test_build_agent_exposes_all_three_tools():
    """The orchestrator's Agent exposes hello_world, on_arrival_event, and
    search_and_invoke."""
    agent = build_agent()
    tool_names = {getattr(t, "tool_name", getattr(t, "__name__", str(t))) for t in agent.tool_names}
    # Strands exposes registered tool names via agent.tool_names (a list of
    # strings) in recent versions. Fall back to checking the agent's tool
    # registry directly if the attribute shape differs.
    assert "hello_world" in tool_names or "hello_world" in str(agent.tool_names)
    assert "on_arrival_event" in tool_names or "on_arrival_event" in str(agent.tool_names)
    assert "search_and_invoke" in tool_names or "search_and_invoke" in str(agent.tool_names)


def test_build_orchestrator_card_minimal_hello_world_only():
    """The orchestrator's own A2A card advertises only the hello-world skill,
    not the internal tools, which are not A2A skills a peer discovers."""
    import json

    card_json = build_orchestrator_card("https://example.invalid/a2a")
    card = json.loads(card_json)
    # The card is built via the a2a-sdk AgentCard model, which emits the full
    # A2A schema version "0.3.0". The live registry accepts that value at
    # create_registry_record and rejects "0.3".
    assert card["protocolVersion"] == "0.3.0"
    skill_names = {s["name"] for s in card["skills"]}
    assert skill_names == {"hello-world"}


def test_build_orchestrator_card_skill_entries_have_full_a2a_sdk_shape():
    """Every skill entry carries id, name, description, and tags, the full
    a2a-sdk AgentSkill required shape (RC-03), matching how U3/U4 assert the
    card shape. This checks shape only and does not change which skills are
    advertised (still only hello-world)."""
    import json

    card_json = build_orchestrator_card("https://example.invalid/a2a")
    card = json.loads(card_json)
    assert len(card["skills"]) == 1  # exactly one skill advertised
    for skill in card["skills"]:
        assert set(skill.keys()) >= {"id", "name", "description", "tags"}
        assert skill["id"]  # non-empty
        assert skill["name"]  # non-empty
        assert skill["description"]  # non-empty
        assert isinstance(skill["tags"], list) and skill["tags"]  # non-empty list


def test_register_orchestrator_derives_recordid_from_recordarn(monkeypatch):
    """The registration call uses U0's recordArn+status response shape and
    derives recordId via record_id_from_arn(), never reading
    response["recordId"] directly."""

    captured_call = {}

    class _FakeControlClient:
        def create_registry_record(self, **kwargs):
            captured_call.update(kwargs)
            return {
                "recordArn": (
                    "arn:aws:agent-registry:us-east-1:111111111111:"
                    "registry/abcdEFGH1234/record/wxyzABCD5678"
                ),
                "status": "CREATING",
            }

    record_id = register_orchestrator(
        _FakeControlClient(),
        registry_id="abcdEFGH1234",
        a2a_endpoint_url="https://example.invalid/a2a",
    )

    assert record_id == "wxyzABCD5678"
    assert captured_call["recordType"] == "AGENT"
    assert captured_call["registryId"] == "abcdEFGH1234"
    assert "a2aAgentCard" in captured_call["descriptors"]
    assert captured_call["descriptors"]["a2aAgentCard"]["dataSchemaVersion"] == "0.3"


def test_register_orchestrator_propagates_failure_uncaught(monkeypatch):
    """A failed create_registry_record call propagates uncaught. No
    try/except swallows it and no retry is attempted."""

    class _FailingControlClient:
        call_count = 0

        def create_registry_record(self, **kwargs):
            self.call_count += 1
            raise RuntimeError("simulated ClientError: ValidationException")

    client = _FailingControlClient()
    with pytest.raises(RuntimeError, match="simulated ClientError"):
        register_orchestrator(
            client, registry_id="abcdEFGH1234", a2a_endpoint_url="https://example.invalid/a2a"
        )

    # One attempt, no retry loop.
    assert client.call_count == 1


def test_register_orchestrator_reuses_existing_record_on_name_conflict():
    """Re-runnability: the orchestrator is standing, so a re-run that collides
    on the deterministic 'orchestrator' name reuses the existing record (looked
    up by name) instead of failing. Create raises the modeled
    ConflictException('already exists') and registration returns the existing id."""
    from botocore.exceptions import ClientError

    class _ConflictThenList:
        def create_registry_record(self, **kwargs):
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConflictException",
                        "Message": "A record with name 'orchestrator' already exists in registry R",
                    }
                },
                "CreateRegistryRecord",
            )

        def list_registry_records(self, **kwargs):
            return {
                "registryRecords": [
                    {"name": "peer-greeter", "recordId": "PEER"},
                    {"name": "orchestrator", "recordId": "EXISTING_ORCH"},
                ]
            }

    record_id = register_orchestrator(
        _ConflictThenList(), registry_id="R", a2a_endpoint_url="https://example.invalid/a2a"
    )
    assert record_id == "EXISTING_ORCH"


def test_register_orchestrator_propagates_non_conflict_clienterror():
    """The reuse guard is narrow. A non-conflict ClientError (e.g. a validation
    error) still propagates uncaught, so the fail-fast contract holds."""
    from botocore.exceptions import ClientError

    class _ValidationBoom:
        def create_registry_record(self, **kwargs):
            raise ClientError(
                {"Error": {"Code": "ValidationException", "Message": "bad input"}},
                "CreateRegistryRecord",
            )

    with pytest.raises(ClientError):
        register_orchestrator(
            _ValidationBoom(), registry_id="R", a2a_endpoint_url="https://example.invalid/a2a"
        )
