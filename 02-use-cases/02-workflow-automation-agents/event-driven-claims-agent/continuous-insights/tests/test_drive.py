"""Tests for how drive.py builds and sends claims."""

import io
import json

import drive


class FakeRuntime:
    """Stands in for the bedrock-agentcore client; records the call and returns a readable stream."""

    def __init__(self):
        self.calls = []

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        return {"response": io.BytesIO(b"{}")}


def test_plan_cycles_through_the_scenarios_from_the_offset():
    names = [sc.name for sc in drive.SCENARIOS]
    plan = [sc.name for sc in drive.build_plan(len(names) + 2, offset=1)]
    assert plan[0] == names[1]
    assert plan[len(names)] == names[1]  # wrapped around after one full cycle
    assert len(plan) == len(names) + 2


def test_the_mix_covers_approval_and_human_review_cases():
    prompts = " ".join(sc.prompt for sc in drive.SCENARIOS)
    assert "POL-12345" in prompts and "$150,000" in prompts and "pol-12345" in prompts


def test_every_scenario_has_a_name_a_prompt_and_an_email():
    assert all(sc.name.strip() and sc.prompt.strip() and "@" in sc.email for sc in drive.SCENARIOS)
    assert len({sc.name for sc in drive.SCENARIOS}) == len(drive.SCENARIOS)


def test_most_scenarios_use_the_policy_holders_email_and_one_does_not():
    holders = {
        "POL-12345": "john.smith@example.com",
        "POL-67890": "jane.doe@example.com",
        "POL-11111": "bob.j@example.com",
    }
    matching = [
        sc
        for sc in drive.SCENARIOS
        if any(num in sc.prompt.upper() and email == sc.email for num, email in holders.items())
    ]
    assert len(matching) >= 4
    assert any(sc.name == "email-not-on-policy" and sc.email not in holders.values() for sc in drive.SCENARIOS)


def test_run_session_sends_the_claim_payload_and_a_valid_session_id():
    client = FakeRuntime()
    scenario = drive.SCENARIOS[0]
    session_id = drive.run_session(client, "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/x", scenario)
    call = client.calls[0]
    assert call["runtimeSessionId"] == session_id
    assert 33 <= len(session_id) <= 64  # runtime session ids need at least 33 characters
    assert call["qualifier"] == "DEFAULT"
    payload = json.loads(call["payload"])
    assert payload["prompt"] == scenario.prompt
    assert payload["claimant_email"] == scenario.email


def test_each_call_gets_its_own_session_id():
    client = FakeRuntime()
    ids = {drive.run_session(client, "arn", drive.SCENARIOS[3]) for _ in range(5)}
    assert len(ids) == 5
