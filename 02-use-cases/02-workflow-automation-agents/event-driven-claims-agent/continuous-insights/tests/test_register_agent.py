"""Tests for register_agent.py: runtime lookup and the state it records."""

import pytest
import register_agent as reg

RUNTIME = {
    "agentRuntimeName": "ClaimsAgent_claimsagent",
    "agentRuntimeId": "ClaimsAgent_claimsagent-AbC123",
    "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/ClaimsAgent_claimsagent-AbC123",
    "status": "READY",
}


class FakeControl:
    def __init__(self, runtimes):
        self.runtimes = runtimes

    def get_paginator(self, _):
        outer = self

        class Paginator:
            def paginate(self):
                yield {"agentRuntimes": outer.runtimes[:1]}
                yield {"agentRuntimes": outer.runtimes[1:]}

        return Paginator()


def test_finds_the_runtime_by_exact_name_across_pages():
    other = {**RUNTIME, "agentRuntimeName": "Other", "agentRuntimeId": "Other-1"}
    assert reg.find_runtime(FakeControl([other, RUNTIME]), "ClaimsAgent_claimsagent") == RUNTIME


def test_a_missing_runtime_exits_with_the_command_to_list_them():
    with pytest.raises(SystemExit, match="list-agent-runtimes"):
        reg.find_runtime(FakeControl([RUNTIME]), "Nope")


def test_a_runtime_that_is_not_ready_exits():
    with pytest.raises(SystemExit, match="not READY"):
        reg.find_runtime(FakeControl([{**RUNTIME, "status": "CREATING"}]), "ClaimsAgent_claimsagent")


def test_state_holds_the_names_the_other_scripts_use():
    state = reg.build_state(RUNTIME, "us-east-1", "111111111111")
    assert state["service_name"] == "ClaimsAgent_claimsagent.DEFAULT"
    log_group = "/aws/bedrock-agentcore/runtimes/ClaimsAgent_claimsagent-AbC123-DEFAULT"  # pragma: allowlist secret
    assert state["log_group"] == log_group
    assert state["runtime_arn"] == RUNTIME["agentRuntimeArn"]
    assert state["region"] == "us-east-1" and state["account_id"] == "111111111111"
