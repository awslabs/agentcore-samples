"""Tests for the polling and retry helpers every script uses to wait."""

import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import common


@pytest.fixture(autouse=True)
def no_real_waiting(monkeypatch):
    """Record the pauses instead of sleeping, so the tests run instantly."""
    pauses: list[float] = []
    monkeypatch.setattr(common, "_pause", pauses.append)
    return pauses


def client_error(code: str, message: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "TestOperation")


def test_poll_returns_the_first_ready_value(no_real_waiting):
    answers = iter([None, None, "ready"])
    assert common.poll(lambda: next(answers), timeout_s=60, interval_s=5, what="a thing") == "ready"
    assert no_real_waiting == [5, 5]


def test_poll_checks_once_even_with_a_zero_timeout():
    assert common.poll(lambda: "ready", timeout_s=0, interval_s=5, what="a thing") == "ready"


def test_poll_exits_with_a_clear_message_on_timeout():
    with pytest.raises(SystemExit, match="Timed out after 0 seconds waiting for the store"):
        common.poll(lambda: None, timeout_s=0, interval_s=5, what="the store")


def test_poll_can_return_none_on_timeout_when_the_caller_reports_it():
    assert common.poll(lambda: None, timeout_s=0, interval_s=5, what="a thing", timeout_ok=True) is None


def test_retry_call_retries_only_the_accepted_error(no_real_waiting):
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise client_error("ValidationException", "role cannot be assumed")
        return "created"

    result = common.retry_call(
        flaky, should_retry=lambda e: "cannot be assumed" in e.response["Error"]["Message"], attempts=4, delay_s=10
    )
    assert result == "created"
    assert no_real_waiting == [10, 20]  # the delay grows with each attempt


def test_retry_call_raises_other_errors_immediately(no_real_waiting):
    def broken():
        raise client_error("AccessDeniedException", "not allowed")

    with pytest.raises(ClientError):
        common.retry_call(
            broken, should_retry=lambda e: "cannot be assumed" in e.response["Error"]["Message"], attempts=4, delay_s=10
        )
    assert no_real_waiting == []


def test_retry_call_gives_up_after_the_last_attempt(no_real_waiting):
    def always():
        raise client_error("ValidationException", "role cannot be assumed")

    with pytest.raises(ClientError):
        common.retry_call(always, should_retry=lambda e: True, attempts=3, delay_s=1)
    assert no_real_waiting == [1, 2]


def test_validate_name_rules():
    assert common.validate_name("ClaimsAgent") == "ClaimsAgent"
    with pytest.raises(SystemExit):
        common.validate_name("claims-agent")
    with pytest.raises(SystemExit):
        common.validate_name("1desk")


def test_root_is_the_parent_of_the_scripts_folder():
    assert common.HERE.name == "scripts"
    assert common.ROOT == common.HERE.parent


def test_state_file_lives_under_the_repository_root_not_scripts():
    path = common.state_path("ClaimsAgent")
    assert path == common.ROOT / "agent_state_ClaimsAgent.json"
    assert path.is_relative_to(common.ROOT)
    assert common.HERE not in path.parents


def test_missing_state_message_uses_the_scripts_path(monkeypatch, tmp_path):
    monkeypatch.setattr(common, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        common.load_state("Nope")
    assert "python scripts/register_agent.py --name Nope" in str(exit_info.value)
