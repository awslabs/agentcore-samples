"""Shared helpers for the scripts in this folder (state file, boto3 clients, name rules)."""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

T = TypeVar("T")

HERE = Path(__file__).resolve().parent  # the scripts/ folder
ROOT = HERE.parent  # the repository root: user data (state, session logs, exports) lives here, not in scripts/
NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,47}$")
DEFAULT_REGION = "us-east-1"

_RETRY = Config(retries={"max_attempts": 5, "mode": "adaptive"})


def _pause(seconds: float) -> None:
    """The only place this sample waits. poll() and retry_call() bound it with a deadline or attempt limit."""
    time.sleep(seconds)  # nosemgrep: arbitrary-sleep (bounded polling of eventually consistent APIs)


def poll(
    check: Callable[[], T | None], *, timeout_s: float, interval_s: float, what: str, timeout_ok: bool = False
) -> T | None:
    """Call check() until it returns something other than None, or the deadline passes.

    check() runs at least once. On timeout this exits with a clear message, or returns None
    when timeout_ok is True so the caller can report its own result.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        result = check()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            if timeout_ok:
                return None
            sys.exit(f"Timed out after {int(timeout_s)} seconds waiting for {what}.")
        _pause(interval_s)


def retry_call(
    call: Callable[[], T], *, should_retry: Callable[[ClientError], bool], attempts: int, delay_s: float
) -> T:
    """Run call(); retry only errors that should_retry accepts, with a growing delay, up to `attempts` tries."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except ClientError as err:
            if attempt == attempts or not should_retry(err):
                raise
            _pause(delay_s * attempt)
    raise AssertionError("unreachable")  # the loop either returns or raises


def validate_name(name: str) -> str:
    """Runtime and evaluation-config names: letter first, then letters/digits/underscore, max 48."""
    if not NAME_PATTERN.match(name):
        sys.exit(f"--name {name!r} is invalid: use 1-48 chars, start with a letter, letters/digits/underscore only")
    return name


def state_path(name: str) -> Path:
    return ROOT / f"agent_state_{name}.json"


def load_state(name: str) -> dict:
    path = state_path(name)
    if not path.exists():
        sys.exit(f"{path.name} not found. Run: python scripts/register_agent.py --name {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(name: str, state: dict) -> None:
    state_path(name).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def session(region: str) -> boto3.Session:
    return boto3.Session(region_name=region)


def client(region: str, service: str):
    """boto3 client with adaptive retries."""
    return session(region).client(service, config=_RETRY)


def evaluation_clients(region: str):
    """Return (control_plane, data_plane) clients for Continuous Insights.

    The Agent Insight Store and online evaluation operations are in bedrock-agentcore-control; the Agent
    Insight operations are in bedrock-agentcore. Both need boto3 1.44.5 or later.
    """
    control = client(region, "bedrock-agentcore-control")
    data = client(region, "bedrock-agentcore")
    if not hasattr(data, "list_agent_insights") or not hasattr(control, "get_agent_insight_store"):
        sys.exit(
            "This boto3 build has no Agent Insight operations. Upgrade to boto3 1.44.5 or later: "
            "pip install -r requirements.txt --upgrade"
        )
    return control, data
