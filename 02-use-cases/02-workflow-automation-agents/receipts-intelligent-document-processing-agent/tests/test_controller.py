"""Unit tests for the account-level ladder controller's pure decision logic
(Phase 6 sub-step 3). Tests `decide_next_rung` only — no AWS, no mocks. The AWS I/O
(read latest config, cooldown, write new version) is exercised live by
tests/test_e2e_controller_live.py."""

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

# The controller lives under lambdas/ (not on the package path), so load it directly.
_HANDLER = os.path.join(os.path.dirname(__file__), "..", "lambdas", "controller", "handler.py")
_spec = importlib.util.spec_from_file_location("controller_handler", _HANDLER)
controller = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(controller)
decide_next_rung = controller.decide_next_rung


def test_alarm_steps_down_one_rung():
    assert decide_next_rung("L0", "ALARM") == "L1"
    assert decide_next_rung("L1", "ALARM") == "L2"
    assert decide_next_rung("L3", "ALARM") == "L4"


def test_ok_steps_up_one_rung():
    assert decide_next_rung("L4", "OK") == "L3"
    assert decide_next_rung("L2", "OK") == "L1"
    assert decide_next_rung("L1", "OK") == "L0"


def test_alarm_clamps_at_bottom():
    # Already at L4 (defer) — can't step further down.
    assert decide_next_rung("L4", "ALARM") is None


def test_ok_clamps_at_top():
    # Already at L0 (full) — can't step further up.
    assert decide_next_rung("L0", "OK") is None


def test_insufficient_data_is_noop():
    assert decide_next_rung("L1", "INSUFFICIENT_DATA") is None
    assert decide_next_rung("L1", "") is None
    assert decide_next_rung("L1", "WHATEVER") is None


def test_one_rung_at_a_time():
    # Each call moves at most a single rung — never a jump.
    assert decide_next_rung("L0", "ALARM") == "L1"  # not L2/L3/L4
    assert decide_next_rung("L4", "OK") == "L3"  # not L0


def test_unknown_current_rung_defaults_to_l0():
    # A malformed activeRung is treated as L0 (safe top), so ALARM -> L1.
    assert decide_next_rung("BOGUS", "ALARM") == "L1"
    assert decide_next_rung("BOGUS", "OK") is None  # L0 can't step up
