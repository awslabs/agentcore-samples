"""Unit tests for payload parsing — the one piece of real logic in the Phase 1
stub. No AWS, no runtime; pure-function tests."""

import json

import pytest

from parsing import parse_payload

pytestmark = pytest.mark.unit


def test_plain_dict_passthrough():
    p = {"s3_uri": "s3://b/r.jpg", "user_id": "user-001"}
    assert parse_payload(p) == p


def test_json_string_is_parsed():
    p = json.dumps({"s3_uri": "s3://b/r.jpg", "user_id": "u"})
    assert parse_payload(p) == {"s3_uri": "s3://b/r.jpg", "user_id": "u"}


def test_agentcore_dev_wrapper_is_unwrapped():
    # `agentcore dev` delivers {"prompt": "<json>"} hiding the real payload.
    inner = {"s3_uri": "s3://b/r.jpg", "user_id": "u"}
    wrapped = {"prompt": json.dumps(inner)}
    assert parse_payload(wrapped) == inner


def test_natural_language_prompt_preserved():
    out = parse_payload({"prompt": "process my coffee receipt please"})
    assert out == {"prompt": "process my coffee receipt please"}


def test_bare_natural_language_string():
    out = parse_payload("just some text")
    assert out == {"prompt": "just some text"}


def test_real_payload_with_prompt_key_not_clobbered():
    # If s3_uri is already present, don't try to unwrap a prompt.
    p = {"s3_uri": "s3://b/r.jpg", "user_id": "u", "prompt": "hint"}
    assert parse_payload(p) == p


@pytest.mark.parametrize("bad", [123, 4.5, None])
def test_non_str_non_dict_coerced_to_prompt(bad):
    out = parse_payload(bad)
    assert "prompt" in out
