"""Shipped console approval trace after a successful new run and session rotation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "case",
    (
        "polled",
        "delayed",
        "held_success",
        "held_empty",
        "held_403",
        "held_500",
        "held_malformed",
        "held_wrong_id",
        "invalidate_bob_success",
        "invalidate_bob_error",
        "invalidate_proof_success",
        "invalidate_proof_error",
        "invalidate_reset_success",
        "invalidate_reset_error",
        "invalidate_stage_success",
        "invalidate_stage_error",
        "invalidate_new_run_success",
        "invalidate_new_run_error",
    ),
)
def test_new_owned_approval_trace_survives_session_rotation_in_chrome(case):
    python = os.environ.get("UI_BROWSER_PYTHON")
    if not python:
        pytest.skip("set UI_BROWSER_PYTHON to installed offline Playwright")
    command = [python, "tests/approval_trace_browser.py"]
    if case == "delayed":
        command.append("--late-events")
    elif case.startswith("held_"):
        command.extend(("--hold-full", "--case=" + case.removeprefix("held_")))
    elif case.startswith("invalidate_"):
        action, response = case.removeprefix("invalidate_").rsplit("_", 1)
        command.extend(("--hold-full", "--invalidate=" + action, "--reply=" + response))
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if case in ("polled", "delayed", "held_success"):
        assert "one loopback approval POST" in result.stdout
        assert "new authorized run/events/Auto/Step" in result.stdout
    elif case.startswith("invalidate_"):
        assert "stale new-owner full trace reply ignored" in result.stdout
    else:
        assert "one owned full trace GET returns explicit empty/error" in result.stdout
