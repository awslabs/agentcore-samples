"""Unit tests for the conversational-identity token logic (the IDOR fix).

Tests the PURE encode/split/expiry functions — no AWS. The KMS GenerateMac/VerifyMac
round-trip + the cross-user rejection are covered live by test_e2e_chat_live.py."""

import time

import pytest

from identity import (
    assemble_token,
    claim_is_valid,
    encode_claim,
    split_token,
)

pytestmark = pytest.mark.unit


def test_encode_claim_is_stable_and_minimal():
    # Same inputs -> identical bytes (so sign and verify MAC the same message).
    a = encode_claim("user-009", 1000)
    b = encode_claim("user-009", 1000)
    assert a == b
    assert b'"user_id":"user-009"' in a and b'"exp":1000' in a


def test_split_roundtrips_assemble():
    claim = encode_claim("user-001", 2000)
    mac = b"\x01\x02\x03\x04macbytes"
    token = assemble_token(claim, mac)
    c2, m2 = split_token(token)
    assert c2 == claim and m2 == mac


def test_split_rejects_malformed():
    for bad in ["", "noseparator", "only.", ".only", "a.b.c"]:
        with pytest.raises(ValueError):
            split_token(bad)


def test_claim_valid_when_not_expired():
    now = int(time.time())
    claim = encode_claim("user-009", now + 100)
    out = claim_is_valid(claim, now)
    assert out["user_id"] == "user-009"


def test_claim_rejected_when_expired():
    now = int(time.time())
    claim = encode_claim("user-009", now - 1)
    with pytest.raises(ValueError, match="expired"):
        claim_is_valid(claim, now)


def test_claim_rejected_when_missing_fields():
    import json

    with pytest.raises(ValueError):
        claim_is_valid(json.dumps({"user_id": "x"}).encode(), int(time.time()))
    with pytest.raises(ValueError):
        claim_is_valid(json.dumps({"exp": 999999999999}).encode(), int(time.time()))


def test_tampered_user_id_changes_the_signed_bytes():
    # The security property the MAC relies on: changing user_id changes the message
    # that was signed, so a MAC over the original will not verify against the tampered
    # claim. (The actual VerifyMac is live-tested; here we prove the bytes differ.)
    original = encode_claim("user-009", 5000)
    tampered = encode_claim("user-012", 5000)
    assert original != tampered
