"""Actual edge handler + local RSA login flow; no AWS SDK or credentials required."""

import subprocess
from pathlib import Path


def test_edge_login_returns_only_to_same_origin():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "tests/edge_return_paths.mjs"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "edge login, presenter and switch: same-origin attack/positive cases passed"
        in result.stdout
    )


def test_session_ends_before_the_token_enters_its_last_minute():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["node", "tests/gate_session.mjs"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "session ends five minutes before its token" in result.stdout
