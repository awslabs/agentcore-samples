"""Unit tests for the front-door trigger Lambda's pure logic (Phase 7).
Tests build_payload + user_id_from_key only — no AWS, no mocks. The S3 -> EventBridge
-> Runtime wiring is exercised live by tests/test_e2e_frontdoor_live.py."""

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

# The trigger lives under lambdas/ (not on the package path), so load it directly.
# Set the env BEFORE import so DEFAULT_USER_ID is read deterministically.
os.environ.setdefault("DEFAULT_USER_ID", "user-001")
_HANDLER = os.path.join(os.path.dirname(__file__), "..", "lambdas", "trigger", "handler.py")
_spec = importlib.util.spec_from_file_location("trigger_handler", _HANDLER)
trigger = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trigger)


def test_user_id_from_per_user_key():
    # receipts/<user_id>/<file> carries the user in the path.
    assert trigger.user_id_from_key("receipts/alice/receipt-1.png") == "alice"
    assert trigger.user_id_from_key("receipts/user-042/sub/deep.png") == "user-042"


def test_user_id_from_flat_key_falls_back_to_default():
    # receipts/<file> has no user segment -> the seeded default user.
    assert trigger.user_id_from_key("receipts/e2e-abc123.png") == "user-001"


def test_user_id_from_empty_or_bare_key_is_default():
    assert trigger.user_id_from_key("") == "user-001"
    assert trigger.user_id_from_key("receipts/") == "user-001"


def test_build_payload_shape():
    p = trigger.build_payload("receipts-inbox-123-us-west-2", "receipts/alice/r.png")
    assert p == {
        "s3_uri": "s3://receipts-inbox-123-us-west-2/receipts/alice/r.png",
        "user_id": "alice",
    }


def test_build_payload_flat_key_default_user():
    p = trigger.build_payload("my-bucket", "receipts/r.png")
    assert p["s3_uri"] == "s3://my-bucket/receipts/r.png"
    assert p["user_id"] == "user-001"
