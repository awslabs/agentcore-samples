"""Real shipped-console replay of the sanitized, tenant-owned 19-event V3 run."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_fresh_recorded_trace_hydrates_in_chrome_without_app_mutations():
    python = os.environ.get("UI_BROWSER_PYTHON")
    if not python:
        pytest.skip("set UI_BROWSER_PYTHON to installed offline Playwright")
    result = subprocess.run(
        [python, "tests/recorded_trace_browser.py"],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "19-hop allowlisted run" in result.stdout
    assert "no app mutation or external HTTP" in result.stdout
