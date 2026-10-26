# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ArrivalAwareExecutor routing decision.

The routing decision (is this inbound message a structured arrival signal, or
ordinary conversation?) is the pure, testable core. The full A2A execute()
wiring (TaskUpdater, EventQueue, task lifecycle) is covered by the live
end-to-end run, not mocked here. This is the unit layer of the pyramid.
"""

from __future__ import annotations

from types import SimpleNamespace

from a2a.types import DataPart, Part, TextPart

from agent_registry_sample.orchestrator.arrival_executor import (
    ARRIVAL_EVENT_KEY,
    _extract_arrival_payload,
)


def _ctx(parts):
    return SimpleNamespace(message=SimpleNamespace(parts=parts))


def test_arrival_datapart_is_extracted():
    """A DataPart carrying the arrivalEvent key yields the arrival payload, so
    execute() routes it to the event path (no LLM)."""
    payload = {"registryRecordId": "rec-1", "registryId": "reg-1"}
    parts = [Part(root=DataPart(data={ARRIVAL_EVENT_KEY: payload}))]
    assert _extract_arrival_payload(_ctx(parts)) == payload


def test_text_message_is_not_an_arrival():
    """An ordinary text message is not an arrival, so execute() falls through
    to the base executor (normal A2A conversation)."""
    parts = [Part(root=TextPart(text="hello, can you help?"))]
    assert _extract_arrival_payload(_ctx(parts)) is None


def test_unrelated_datapart_is_not_an_arrival():
    """A DataPart without the arrivalEvent key falls through to the base
    executor (e.g. an interrupt-response DataPart)."""
    parts = [Part(root=DataPart(data={"interruptResponse": {"interruptId": "x", "response": 1}}))]
    assert _extract_arrival_payload(_ctx(parts)) is None


def test_no_message_or_no_parts_is_not_an_arrival():
    assert _extract_arrival_payload(SimpleNamespace(message=None)) is None
    assert _extract_arrival_payload(_ctx([])) is None


def test_arrival_payload_must_be_a_dict():
    """A malformed arrivalEvent value (not a dict) is ignored rather than
    passed through as a payload."""
    parts = [Part(root=DataPart(data={ARRIVAL_EVENT_KEY: "not-a-dict"}))]
    assert _extract_arrival_payload(_ctx(parts)) is None


def test_arrival_rejects_extra_keys():
    """Strict schema. An arrivalEvent with extra keys beyond
    registryRecordId + registryId is rejected, so a crafted DataPart cannot
    smuggle additional fields into the event path."""
    parts = [
        Part(
            root=DataPart(
                data={
                    ARRIVAL_EVENT_KEY: {
                        "registryRecordId": "rec-1",
                        "registryId": "reg-1",
                        "toolUse": "evil",
                    }
                }
            )
        )
    ]
    assert _extract_arrival_payload(_ctx(parts)) is None


def test_arrival_rejects_missing_field():
    parts = [Part(root=DataPart(data={ARRIVAL_EVENT_KEY: {"registryRecordId": "rec-1"}}))]
    assert _extract_arrival_payload(_ctx(parts)) is None


def test_arrival_rejects_non_string_or_empty_field():
    for bad in (
        {"registryRecordId": "rec-1", "registryId": ""},
        {"registryRecordId": 123, "registryId": "reg-1"},
    ):
        parts = [Part(root=DataPart(data={ARRIVAL_EVENT_KEY: bad}))]
        assert _extract_arrival_payload(_ctx(parts)) is None
