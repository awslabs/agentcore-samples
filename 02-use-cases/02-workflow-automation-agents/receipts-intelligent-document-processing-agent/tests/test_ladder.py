"""Unit tests for the degradation-ladder rung resolution (Phase 6 sub-step 1).
Pure-function tests over resolve_rung — no AWS, no mocks."""

import pytest

from model.ladder import (
    L0_DEFAULT,
    RUNG_ORDER,
    classify_model_error,
    next_rung,
    resolve_rung,
)

pytestmark = pytest.mark.unit


class _ClientError(Exception):
    """Mimics botocore ClientError shape (response.Error.Code)."""

    def __init__(self, code: str):
        super().__init__(f"{code} occurred")
        self.response = {"Error": {"Code": code}}


def test_classify_503_steps():
    assert classify_model_error(_ClientError("ServiceUnavailableException")) == "step"


def test_classify_429_backoff():
    assert classify_model_error(_ClientError("ThrottlingException")) == "backoff"


def test_classify_500_backoff():
    assert classify_model_error(_ClientError("InternalServerException")) == "backoff"


def test_classify_strands_modelthrottled_backoff():
    # Strands wraps 429 as ModelThrottledException (no response attr).
    assert classify_model_error(type("ModelThrottledException", (Exception,), {})()) == "backoff"


def test_classify_other_raises():
    assert classify_model_error(_ClientError("ValidationException")) == "raise"
    assert classify_model_error(ValueError("boom")) == "raise"


def test_next_rung_walks_down():
    assert next_rung("L0") == "L1"
    assert next_rung("L3") == "L4"
    assert next_rung("L4") is None  # bottom
    assert next_rung("nope") is None


def test_rung_order_shape():
    assert RUNG_ORDER == ["L0", "L1", "L2", "L3", "L4"]


CFG = {
    "activeRung": "L0",
    "rungs": {
        "L0": {"model": "global.anthropic.claude-opus-4-8", "features": {"validator": True, "memoryWrite": True}},
        "L2": {"model": "global.anthropic.claude-opus-4-6-v1", "features": {"validator": False, "forceReview": True}},
        "L4": {"features": {"forceReview": True}},
    },
}


def test_resolves_active_rung():
    r = resolve_rung(CFG)
    assert r["rung"] == "L0"
    assert r["model"] == "global.anthropic.claude-opus-4-8"
    assert r["features"]["validator"] is True
    assert r["defer"] is False


def test_explicit_rung_override():
    r = resolve_rung(CFG, "L2")
    assert r["rung"] == "L2"
    assert r["model"] == "global.anthropic.claude-opus-4-6-v1"
    assert r["features"]["validator"] is False
    assert r["features"]["forceReview"] is True


def test_features_merge_over_l0_defaults():
    # L2 only specifies validator + forceReview; the rest fill from L0 defaults.
    r = resolve_rung(CFG, "L2")
    assert r["features"]["dedup"] is True  # from L0 default
    assert r["features"]["memoryRead"] is True  # from L0 default


def test_l4_is_defer_no_model():
    r = resolve_rung(CFG, "L4")
    assert r["rung"] == "L4"
    assert r["defer"] is True  # no model on the rung


def test_unknown_rung_falls_back_to_l0():
    r = resolve_rung(CFG, "L99")
    assert r["rung"] == "L0"
    assert r["model"] == L0_DEFAULT["model"]


def test_malformed_config_is_safe():
    assert resolve_rung(None)["rung"] == "L0"
    assert resolve_rung({})["rung"] == "L0"
    assert resolve_rung({"activeRung": "L0", "rungs": {}})["rung"] == "L0"
