# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the trigger-adapter handler.

The adapter wraps the arrival in an A2A message/send DataPart envelope. A
JSON-RPC error body or a failed task raises, even on an HTTP 200, so the message
flows to SQS retry/DLQ instead of clearing.
"""

from __future__ import annotations

import io
import json

import pytest

from agent_registry_sample.infrastructure.adapter_src import handler


def test_arrival_envelope_is_a2a_message_send_with_datapart():
    raw = handler._arrival_envelope("rec-1", "reg-1")
    env = json.loads(raw)
    assert env["jsonrpc"] == "2.0"
    assert env["method"] == "message/send"
    parts = env["params"]["message"]["parts"]
    assert len(parts) == 1
    part = parts[0]
    assert part["kind"] == "data"
    assert part["data"]["arrivalEvent"] == {"registryRecordId": "rec-1", "registryId": "reg-1"}


def _resp(obj):
    return {"response": io.BytesIO(json.dumps(obj).encode("utf-8"))}


def test_raise_on_jsonrpc_error_body():
    """A JSON-RPC error body on a 200 must raise."""
    with pytest.raises(RuntimeError, match="JSON-RPC error"):
        handler._raise_on_failure(
            "rec-1",
            _resp({"jsonrpc": "2.0", "id": "1", "error": {"code": -32600, "message": "bad"}}),
        )


def test_raise_on_failed_task_state():
    """A task that ended failed must raise, even on a 200."""
    with pytest.raises(RuntimeError, match="failed state"):
        handler._raise_on_failure(
            "rec-1", _resp({"jsonrpc": "2.0", "id": "1", "result": {"status": {"state": "failed"}}})
        )


def test_completed_task_does_not_raise():
    """A normal completed result is a success and must not raise."""
    handler._raise_on_failure(
        "rec-1",
        _resp(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "status": {"state": "completed"},
                    "artifacts": [{"parts": [{"kind": "text", "text": "hello world"}]}],
                },
            }
        ),
    )


def test_non_json_body_does_not_raise():
    """A non-JSON 200 body is tolerated (best-effort), not treated as failure."""
    handler._raise_on_failure("rec-1", {"response": io.BytesIO(b"not json")})
