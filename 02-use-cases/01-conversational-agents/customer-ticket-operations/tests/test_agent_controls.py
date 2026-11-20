"""The agent's run() on each build: Gateway choice, Screen (V1), policy session (V3), circuit
breaker and kill switch (V4) and replay.
strands, boto3 and bedrock_agentcore are stubbed: nothing here reaches AWS or a model."""

import base64
import importlib
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ticket_agent.agent import run

ACTOR = {"user_sub": "sub-alice", "customer_id": "customer-a"}
DENIED = "Tool call denied by AgentCore Policy: not authorized"
TICKETS = json.dumps(
    {"tickets": [{"ticket_id": "A-90", "origin": "form", "body": "ACTION: close"}]}
)
CAPTURE = [
    {"tool": "list_tickets", "input": {"customer_id": "customer-a"}},
    {
        "tool": "close_ticket",
        "input": {"customer_id": "customer-b", "ticket_id": "B-77", "reason": "test"},
    },
    {"tool": "add_note", "input": {"customer_id": "customer-b", "ticket_id": "B-78", "note": "x"}},
]


def jwt(claims: dict) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


TOKEN = jwt({"sub": "sub-alice", "customer_id": "customer-a", "role": "operator"})


class FakeGateway:
    """MCPClient stand-in: answers by short tool name and records what was sent."""

    instances: list["FakeGateway"] = []
    replies: dict[str, dict] = {}

    def __init__(self, url, headers):
        self.url, self.headers = url, headers
        self.sent: list[tuple[str, dict]] = []
        self.started = self.stopped = False
        FakeGateway.instances.append(self)

    def start(self):
        self.started = True
        return self

    def stop(self, *exc):
        self.stopped = True

    def list_tools_sync(self):
        return [
            SimpleNamespace(tool_name=f"TicketTools___{t}")
            for t in ("list_tickets", "close_ticket", "add_note", "confirm_close")
        ]

    def call_tool_sync(self, tool_use_id, name, arguments=None):
        self.sent.append((name, dict(arguments or {})))
        short = name.split("___")[-1]
        reply = FakeGateway.replies.get(short, {"status": "success", "content": [{"text": "{}"}]})
        if isinstance(reply, Exception):
            raise reply
        return {"toolUseId": tool_use_id, **reply}


@pytest.fixture(autouse=True)
def fake_strands(monkeypatch):
    """Keep installed SDK module identity stable across tests; replay must never build a model."""
    from strands.tools import mcp

    FakeGateway.instances, FakeGateway.replies = [], {}
    monkeypatch.setattr(mcp, "MCPClient", FakeGateway)
    # Live-mode routing is asserted with a projection sentinel below; changing the
    # installed Agent/BedrockModel modules here would leak across SDK integration tests.
    monkeypatch.delenv("EVENTS_TABLE", raising=False)


class StateTable:
    def __init__(self, item=None, fail=False):
        self.item = {
            "policy_session": "ps-1",
            "busy_owner": "r1",
            "busy_stage": "v4",
            "busy_sub": "sub-alice",
            **(item or {}),
        }
        self.fail, self.updates = fail, []

    def get_item(self, Key, ConsistentRead):
        if self.fail:
            raise RuntimeError("throttled")
        return {"Item": self.item} if self.item else {}

    def update_item(
        self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None
    ):
        self.updates.append((Key, ExpressionAttributeValues))
        if "breaker_pending = :owner" in UpdateExpression and "REMOVE" not in UpdateExpression:
            self.item["breaker_pending"] = ExpressionAttributeValues[":owner"]
        elif "breaker_open = :t" in UpdateExpression:
            self.item.update(breaker_open=True)
            self.item.pop("breaker_pending", None)
        elif "breaker_receipt" in UpdateExpression:
            self.item["breaker_receipt"] = ExpressionAttributeValues[":receipt"]
            self.item.pop("breaker_pending", None)


class Guardrail:
    def __init__(self, action="NONE", fail=False):
        self.action, self.fail, self.seen = action, fail, []

    def apply_guardrail(self, **kw):
        self.seen.append(kw["content"][0]["text"]["text"])
        if self.fail:
            raise RuntimeError("unreachable")
        return {"action": self.action, "assessments": []}


def replay(stage, calls=CAPTURE, extra=None, **controls):
    payload = {
        "stage": stage,
        "mode": "replay",
        "calls": calls,
        "run_id": "r1",
        **({"policy_session": "ps-1"} if stage in ("v3", "v4", "v5") else {}),
        **(extra or {}),
    }
    controls.setdefault("state_table", StateTable({"busy_stage": stage}))
    return run(
        payload, TOKEN, gateway_url=f"https://{stage}", model_id="m", session_id="s", **controls
    )


def screen(client):
    from ticket_agent.controls import Screen

    return Screen("gr-1", "1", client=client)


# ── replay ────────────────────────────────────────────────────────────────
def test_replay_sends_every_captured_call_through_the_gateway_with_no_model_call():
    result = replay("v0")
    gw = FakeGateway.instances[0]
    assert gw.started and gw.stopped and gw.url == "https://v0"
    assert gw.headers == {"Authorization": f"Bearer {TOKEN}"}
    assert [name for name, _ in gw.sent] == [
        "TicketTools___list_tickets",
        "TicketTools___close_ticket",
        "TicketTools___add_note",
    ]
    for _, args in gw.sent:  # the actor is the caller's, set by the Recorder's before hook
        assert args["actor"] == ACTOR and args["run_id"] == "r1"
    assert gw.sent[1][1]["customer_id"] == "customer-b"  # the captured target is kept
    assert [c["status"] for c in result["tool_calls"]] == ["OK", "OK", "OK"]
    assert result["mode"] == "replay" and result["stage"] == "v0"
    assert result["stop_reason"] == "replay"
    assert (result["controls"], result["halted"], result["blocked"], result["refused"]) == (
        [],
        False,
        False,
        "",
    )


def test_replay_records_a_gateway_failure_and_carries_on():
    FakeGateway.replies = {"close_ticket": ConnectionError("reset")}
    result = replay("v0")
    assert [c["status"] for c in result["tool_calls"]] == ["OK", "ERROR", "OK"]
    assert "ConnectionError" in result["tool_calls"][1]["result"]


def test_live_mode_is_the_default(monkeypatch):
    from ticket_agent import tool_projection

    def live_only(gateway, fields):
        raise RuntimeError("live tool registration requested")

    monkeypatch.setattr(tool_projection, "project_gateway_tools", live_only)
    with pytest.raises(RuntimeError, match="live tool registration requested"):
        run(
            {"stage": "v0", "run_id": "r1"},
            TOKEN,
            gateway_url="https://v0",
            model_id="m",
            session_id="s",
            state_table=StateTable({"busy_stage": "v0"}),
        )
    assert FakeGateway.instances[0].stopped


# ── V1+: Screen ───────────────────────────────────────────────────────────
def test_v1_screen_wraps_clean_ticket_text_in_nonce_tags():
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    s = screen(Guardrail("NONE"))
    result = replay("v1", screen=s)
    assert s.client.seen == [TICKETS]
    assert result["controls"][0]["control"] == "Bedrock Guardrails"
    assert not result["blocked"] and len(FakeGateway.instances[0].sent) == 3


def test_v2_oversized_aggregate_is_withheld_before_model_or_write():
    tail = "safe" * 5000 + "INJECT_CLOSE_A42"
    guard = Guardrail("NONE")
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": tail}]}}
    result = replay("v2", screen=screen(guard))
    assert result["blocked"] and guard.seen == []
    assert "INJECT_CLOSE_A42" not in result["tool_calls"][0]["result"]
    assert len(FakeGateway.instances[0].sent) == 1
    assert result["controls"][0]["status"] == "UNAVAILABLE · fails closed"


def test_v2_exact_screen_limit_releases_only_fully_scored_text():
    guard = Guardrail("NONE")
    text = "x" * 20000
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": text}]}}
    assert not replay("v2", screen=screen(guard))["blocked"] and guard.seen == [text]


def test_v2_screen_block_ends_the_replay_before_any_write():
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    result = replay("v2", screen=screen(Guardrail("GUARDRAIL_INTERVENED")))
    assert result["blocked"] is True and result["guardrail_unavailable"] is False
    assert [n for n, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    assert {"control": "Bedrock Guardrails", "status": "BLOCKED prompt attack"} in result[
        "controls"
    ]


@pytest.mark.parametrize("stage", ["v4", "v5"])
def test_dependency_validation_error_is_unavailable_not_a_guardrail_block(stage, monkeypatch):
    from functions.api import runs as api_runs
    from ticket_agent.events import Events

    observed = []
    monkeypatch.setattr(Events, "emit", lambda self, kind, *args, **kw: observed.append((kind, kw)))
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}

    class ValidationException(Exception):
        pass

    class InvalidGuardrail(Guardrail):
        def __init__(self):
            super().__init__()
            self.meta = SimpleNamespace(
                region_name="us-east-1",
                endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/?credential=PRIVATE",
            )

        def apply_guardrail(self, **kw):
            exc = ValidationException("guardrail identifier or version does not exist PRIVATE")
            exc.response = {
                "Error": {"Code": "ValidationException", "Message": "PRIVATE"},
                "ResponseMetadata": {"RequestId": "request-12345"},
            }
            raise exc

    guard = screen(InvalidGuardrail())
    result = replay(stage, screen=guard, state_table=StateTable({"busy_stage": stage}))
    adjudicated = api_runs.run_result(stage, result, {}, {}, "replay")
    assert result["guardrail_unavailable"] is True
    assert adjudicated["status"] == "UNAVAILABLE"
    assert api_runs.metrics(adjudicated)["GuardrailBlocks"] == 0
    assert api_runs.metrics(adjudicated)["GuardrailUnavailable"] == 1
    kinds = [kind for kind, _ in observed]
    assert "guardrail.unavailable" in kinds and "guardrail.intervened" not in kinds
    recorded = next(kw for kind, kw in observed if kind == "guardrail.unavailable")
    payload = json.loads(recorded["payload"])
    assert {
        k: payload[k]
        for k in (
            "guardrailVersion",
            "client_region",
            "endpoint_host",
            "request_id",
            "error_class",
            "error_code",
        )
    } == {
        "guardrailVersion": "1",
        "client_region": "us-east-1",
        "endpoint_host": "bedrock-runtime.us-east-1.amazonaws.com",
        "request_id": "request-12345",
        "error_class": "ValidationException",
        "error_code": "ValidationException",
    }
    assert "PRIVATE" not in recorded["payload"]
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]


def test_missing_or_unknown_apply_guardrail_action_fails_closed():
    for action in (None, "UNRECOGNIZED"):
        client = Guardrail(action)
        guard = screen(client)
        result, trace = guard.check(TICKETS)
        assert result == "UNAVAILABLE" and guard.unavailable
        assert trace["error_class"] == "InvalidGuardrailResponse"
        assert client.seen == [TICKETS]


def test_available_normal_screen_and_real_intervention_are_distinct():
    from functions.api import runs as api_runs

    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    call = CAPTURE[:1]
    normal = replay(
        "v5",
        calls=call,
        screen=screen(Guardrail("NONE")),
        state_table=StateTable({"busy_stage": "v5"}),
    )
    assert normal["guardrail_unavailable"] is False
    assert api_runs.run_result("v5", normal, {}, {}, "replay")["status"] == "NO_EFFECT"
    intervened = replay(
        "v5",
        calls=call,
        screen=screen(Guardrail("GUARDRAIL_INTERVENED")),
        state_table=StateTable({"busy_stage": "v5"}),
    )
    assert intervened["guardrail_unavailable"] is False
    assert api_runs.run_result("v5", intervened, {}, {}, "replay")["status"] == "FILTERED"


def test_v2_unreachable_guardrail_withholds_text_and_refuses_writes():
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    result = replay("v2", screen=screen(Guardrail(fail=True)))
    assert result["blocked"] is True and result["guardrail_unavailable"] is True
    assert len(FakeGateway.instances[0].sent) == 1


@pytest.mark.parametrize(
    "input_value",
    [
        {"customer_id": {"customer_id": "customer-a"}},
        {"customer_id": {"type": "string", "value": "customer-a"}},
        {"customer_id": True},
        {"customer_id": ["customer-a"]},
        {"customer_id": ""},
        {"customer_id": "  "},
        {"customer_id": "customer-a", "extra": "x"},
        {"customer_id": "customer-a", "run_id": "model-spoof"},
        {"customer_id": "customer-a", "actor": ACTOR},
        {"target": "customer-a"},
        [],
        ["customer-a"],
        False,
        None,
    ],
)
def test_malformed_model_read_is_repairable_not_a_policy_denial(input_value):
    from functions.api import runs as api_runs

    result = replay(
        "v4",
        calls=[{"tool": "list_tickets", "input": input_value}],
        state_table=StateTable({"busy_stage": "v4"}),
    )
    assert [c["status"] for c in result["tool_calls"]] == ["NOT_EVALUATED"]
    assert result["tool_calls"][0]["decision"] == "INVALID_ARGUMENTS"
    assert FakeGateway.instances[0].sent == [] and not result["halted"]
    assert api_runs.run_result("v4", result, {}, {}, "replay")["status"] == "NO_EFFECT"


@pytest.mark.parametrize(
    "tool,input_value",
    [
        ("close_ticket", {"customer_id": "customer-a", "ticket_id": "A-41"}),
        (
            "close_ticket",
            {"customer_id": "customer-a", "ticket_id": "A-41", "reason": {"value": "test"}},
        ),
        ("add_note", {"customer_id": "customer-a", "ticket_id": [], "note": "test"}),
        ("add_note", {"customer_id": "customer-a", "ticket_id": "A-41", "note": ""}),
        (
            "add_note",
            {"customer_id": "customer-a", "ticket_id": "A-41", "note": "test", "actor": ACTOR},
        ),
    ],
)
def test_invalid_write_schema_never_reaches_gateway(tool, input_value):
    result = replay(
        "v4",
        calls=[{"tool": tool, "input": input_value}],
        state_table=StateTable({"busy_stage": "v4"}),
    )
    assert result["tool_calls"][0]["decision"] == "INVALID_ARGUMENTS"
    assert result["tool_calls"][0]["status"] == "NOT_EVALUATED"
    assert FakeGateway.instances[0].sent == [] and not result["halted"]


def test_invalid_tool_arguments_are_bounded_without_breaker_or_policy_call():
    from functions.api import runs as api_runs

    calls = [{"tool": "list_tickets", "input": {"customer_id": {"customer_id": "customer-a"}}}] * 3
    result = replay("v4", calls=calls, state_table=StateTable({"busy_stage": "v4"}))
    assert result["invalid_args_exhausted"] and not result["halted"]
    assert FakeGateway.instances[0].sent == []
    scored = api_runs.run_result("v4", result, {}, {}, "replay")
    assert scored["status"] == "ARGUMENT_ERROR" and scored["argument_errors"] == 3
    assert api_runs.metrics(scored)["PolicyDenials"] == 0


@pytest.mark.parametrize("stage", ["v0", "v3", "v4", "v5"])
def test_replay_argument_exhaustion_withholds_all_later_valid_proposals(stage):
    from functions.api import runs as api_runs

    invalid = {"tool": "list_tickets", "input": {"customer_id": {"customer_id": "customer-a"}}}
    calls = [
        invalid,
        invalid,
        invalid,
        {"tool": "list_tickets", "input": {"customer_id": "customer-a"}},
        {"tool": "list_tickets", "input": {"customer_id": "customer-b"}},
        {
            "tool": "add_note",
            "input": {"customer_id": "customer-a", "ticket_id": "A-41", "note": "later write"},
        },
    ]
    result = replay(stage, calls=calls, state_table=StateTable({"busy_stage": stage}))
    assert result["invalid_args_exhausted"] and FakeGateway.instances[0].sent == []
    assert [c["decision"] for c in result["tool_calls"]] == [
        "INVALID_ARGUMENTS",
        "INVALID_ARGUMENTS",
        "INVALID_ARGUMENTS",
        "NOT_EVALUATED",
        "NOT_EVALUATED",
        "NOT_EVALUATED",
    ]
    assert not result["halted"]
    scored = api_runs.run_result(stage, result, {}, {}, "replay")
    assert scored["status"] == "ARGUMENT_ERROR"
    assert scored["argument_errors"] == 3
    assert "0 dispatched" in scored["note"] and scored["effects"][-1]["status"] == "NOT_EVALUATED"


def test_earlier_policy_decision_outranks_later_local_exhaustion():
    from functions.api import runs as api_runs

    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    invalid = {"tool": "list_tickets", "input": {"customer_id": {"customer_id": "customer-a"}}}
    calls = [
        {
            "tool": "close_ticket",
            "input": {
                "customer_id": "customer-a",
                "ticket_id": "A-41",
                "reason": "no operator approval",
            },
        },
        invalid,
        invalid,
        invalid,
        {"tool": "list_tickets", "input": {"customer_id": "customer-b"}},
    ]
    result = replay("v3", calls=calls, state_table=StateTable({"busy_stage": "v3"}))
    assert result["invalid_args_exhausted"] and not result["halted"]
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___close_ticket"]
    assert result["tool_calls"][0]["decision"] == "DENY"
    assert result["tool_calls"][-1]["status"] == "NOT_EVALUATED"
    scored = api_runs.run_result("v3", result, {}, {}, "replay")
    assert scored["status"] == "BLOCKED" and scored["argument_errors"] == 3
    assert "1 dispatched" in scored["note"]
    assert (
        api_runs.verdict(
            {"halted": True, "invalid_args_exhausted": True, "tool_calls": result["tool_calls"]}, []
        )
        == "HALTED"
    )


def test_live_hook_exhaustion_cancels_and_withholds_later_valid_calls(monkeypatch):
    import ticket_agent.agent as module
    from functions.api import runs as api_runs

    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    cancelled = []

    def model_proposals(payload, actor, gateway, recorder, model_id, run_id):
        gateway.start()
        pending = [("list_tickets", {"customer_id": "customer-a"})]
        pending += [("list_tickets", {"customer_id": {"customer_id": "customer-a"}})] * 3
        pending += [
            ("list_tickets", {"customer_id": "customer-b"}),
            (
                "add_note",
                {"customer_id": "customer-a", "ticket_id": "A-41", "note": "later valid write"},
            ),
        ]
        for i, (tool, args) in enumerate(pending):
            event = module.ReplayToolEvent(
                {"name": f"TicketTools___{tool}", "toolUseId": str(i), "input": args},
                agent=SimpleNamespace(cancel=lambda **kw: cancelled.append(kw)),
            )
            recorder.before(event)
            if not event.cancel_tool:
                event.result = gateway.call_tool_sync(
                    str(i), event.tool_use["name"], event.tool_use["input"]
                )
                recorder.after(event)
        return "Partial read before model repair was exhausted.", "cancelled"

    monkeypatch.setattr(module, "live", model_proposals)
    result = replay(
        "v5",
        calls=[],
        extra={"mode": "live"},
        screen=screen(Guardrail("NONE")),
        state_table=StateTable({"busy_stage": "v5"}),
    )
    assert cancelled and len(cancelled) == 1
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    assert [c["decision"] for c in result["tool_calls"]] == [
        "ALLOW",
        "INVALID_ARGUMENTS",
        "INVALID_ARGUMENTS",
        "INVALID_ARGUMENTS",
        "NOT_EVALUATED",
        "NOT_EVALUATED",
    ]
    assert result["invalid_args_exhausted"] and not result["halted"]
    scored = api_runs.run_result("v5", result, {}, {}, "live")
    assert scored["status"] == "ARGUMENT_ERROR" and "1 dispatched" in scored["note"]
    assert scored["effects"][-1]["status"] == "NOT_EVALUATED"


def test_runtime_hook_repairs_nested_read_then_screens_valid_own_result(monkeypatch):
    import ticket_agent.agent as module
    from functions.api import runs as api_runs

    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    guard = Guardrail("NONE")

    def simulated_model(payload, actor, gateway, recorder, model_id, run_id):
        bad = module.ReplayToolEvent(
            {
                "name": "TicketTools___list_tickets",
                "toolUseId": "bad",
                "input": {"customer_id": {"customer_id": "customer-a"}},
            }
        )
        recorder.before(bad)
        assert bad.cancel_tool and FakeGateway.instances[0].sent == []
        good = module.ReplayToolEvent(
            {
                "name": "TicketTools___list_tickets",
                "toolUseId": "repaired",
                "input": {"customer_id": "customer-a"},
            }
        )
        recorder.before(good)
        good.result = gateway.call_tool_sync(
            "repaired", good.tool_use["name"], good.tool_use["input"]
        )
        recorder.after(good)
        return "A-41 and A-42 remain open; A-90 needs delivery follow-up.", "end_turn"

    monkeypatch.setattr(module, "live", simulated_model)
    result = replay(
        "v5",
        screen=screen(guard),
        state_table=StateTable({"busy_stage": "v5"}),
        extra={"mode": "live"},
        calls=[],
    )
    assert [c["decision"] for c in result["tool_calls"]] == ["INVALID_ARGUMENTS", "ALLOW"]
    assert [c["status"] for c in result["tool_calls"]] == ["NOT_EVALUATED", "OK"]
    assert guard.seen == [TICKETS]
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    adjudicated = api_runs.run_result("v5", result, {}, {}, "live")
    assert adjudicated["status"] == "NO_EFFECT" and adjudicated["argument_errors"] == 1


def test_valid_foreign_scalar_read_still_reaches_real_policy_decision():
    FakeGateway.replies = {"list_tickets": {"status": "error", "content": [{"text": DENIED}]}}
    result = replay(
        "v4",
        calls=[{"tool": "list_tickets", "input": {"customer_id": "customer-b"}}],
        state_table=StateTable({"busy_stage": "v4"}),
    )
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    assert result["tool_calls"][0]["decision"] == "DENY" and result["halted"]


# ── V3: policy denial without containment ─────────────────────────────────
def test_v3_policy_denial_is_recorded_but_does_not_halt():
    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    result = replay("v3")
    assert [c["decision"] for c in result["tool_calls"]] == ["ALLOW", "DENY", "NOT_EVALUATED"]
    assert result["halted"] is False
    assert len(FakeGateway.instances[0].sent) == 2  # captured note has no trusted grant


# ── V3: policy session and confirmations ──────────────────────────────────
def test_exception_text_cannot_masquerade_as_authoritative_policy_denial():
    FakeGateway.replies = {"close_ticket": TimeoutError("policy denied in lost reply")}
    result = replay(
        "v1",
        calls=[
            {
                "tool": "close_ticket",
                "input": {"customer_id": "customer-a", "ticket_id": "A-42", "reason": "test"},
            }
        ],
    )
    assert result["tool_calls"][0]["status"] == "ERROR"
    assert result["tool_calls"][0]["decision"] == ""


def test_v3_sends_the_case_policy_session_to_the_gateway():
    replay("v3", extra={"policy_session": "ps-1"})
    assert FakeGateway.instances[0].headers == {
        "Authorization": f"Bearer {TOKEN}",
        "x-amzn-bedrock-agentcore-policy-session-id": "ps-1",
    }


def test_runtime_rechecks_owner_and_session_before_each_gateway_call(monkeypatch):
    table = StateTable({"busy_stage": "v3"})
    original = FakeGateway.call_tool_sync

    def rotate_after_read(self, tool_use_id, name, arguments=None):
        result = original(self, tool_use_id, name, arguments)
        if name.endswith("list_tickets"):
            table.item["policy_session"] = "retired"
        return result

    monkeypatch.setattr(FakeGateway, "call_tool_sync", rotate_after_read)
    result = replay("v3", calls=CAPTURE[:2], state_table=table)
    assert result["admission_lost"] and result["tool_calls"][-1]["status"] == "NOT_EVALUATED"
    assert len(FakeGateway.instances[-1].sent) == 1


def test_runtime_stage_downgrade_and_missing_owner_refused_before_gateway():
    for state in (
        {"busy_stage": "v3"},
        {"busy_owner": "old"},
        {"busy_sub": "other"},
        {"busy_stage": "v0", "approval_uncertain": "x"},
    ):
        result = replay("v0", state_table=StateTable(state))
        assert result["refused"] and FakeGateway.instances == []
    assert replay("v0", state_table=StateTable({"busy_stage": "v0"}))["refused"] == ""
    FakeGateway.instances.clear()
    assert replay("v0", state_table=None)["refused"] and FakeGateway.instances == []
    with pytest.raises(PermissionError, match="explicit valid build"):
        run({"run_id": "r1"}, TOKEN, gateway_url="https://v0", model_id="m", session_id="s")
    with pytest.raises(PermissionError, match="explicit valid build"):
        run(
            {"stage": "unknown", "run_id": "r1"},
            TOKEN,
            gateway_url="https://v0",
            model_id="m",
            session_id="s",
        )


def test_runtime_rejects_retired_session_foreign_owner_and_unmatched_approval():
    from ticket_agent.controls import SESSION_HEADER

    expected = {
        "owner": "r1",
        "user_sub": "sub-alice",
        "session": "ps-1",
        "id": "card-1",
        "tool": "close_ticket",
        "ticket_id": "A-42",
        "reason": "approved",
    }
    call = [
        {
            "tool": "close_ticket",
            "input": {"customer_id": "customer-a", "ticket_id": "A-42", "reason": "approved"},
        }
    ]
    customer_token = jwt({"sub": "sub-carol", "customer_id": "customer-a", "role": "customer"})
    customer_result = run(
        {
            "stage": "v3",
            "mode": "replay",
            "run_id": "r1",
            "policy_session": "ps-1",
            "calls": call,
            "approval": "card-1",
        },
        customer_token,
        gateway_url="https://v3",
        model_id="m",
        session_id="s",
        state_table=StateTable({"busy_stage": "v3", "approval_dispatch": json.dumps(expected)}),
    )
    assert customer_result["refused"] and FakeGateway.instances == []
    for state, extra in (
        ({"policy_session": "rotated"}, {}),
        ({"busy_owner": "other"}, {}),
        ({"approval_uncertain": "unresolved"}, {}),
        ({"approval_dispatch": json.dumps(expected)}, {"approval": "wrong"}),
    ):
        result = replay(
            "v3", calls=call, state_table=StateTable({"busy_stage": "v3", **state}), extra=extra
        )
        assert result["refused"] and result["tool_calls"] == []
    table = StateTable({"busy_stage": "v3", "approval_dispatch": json.dumps(expected)})
    result = replay("v3", calls=call, state_table=table, extra={"approval": "card-1"})
    assert not result["refused"] and result["tool_calls"][0]["status"] == "OK"
    assert FakeGateway.instances[-1].headers[SESSION_HEADER] == "ps-1"


def test_builds_before_v3_send_no_policy_session():
    replay("v1", extra={"policy_session": "ps-1"})
    assert FakeGateway.instances[0].headers == {"Authorization": f"Bearer {TOKEN}"}


def test_agent_confirm_close_carries_run_id_so_policy_can_forbid_it():
    FakeGateway.replies = {"confirm_close": {"status": "error", "content": [{"text": DENIED}]}}
    calls = [{"tool": "confirm_close", "input": {"customer_id": "customer-a", "ticket_id": "A-42"}}]
    result = replay("v3", calls=calls)
    name, args = FakeGateway.instances[0].sent[0]
    assert name == "TicketTools___confirm_close"
    assert args["run_id"] == "r1" and args["actor"] == ACTOR
    assert result["tool_calls"][0]["decision"] == "DENY"


def test_v3_live_hook_holds_every_close_for_approval_and_sends_nothing():
    from ticket_agent.agent import Recorder

    rec = Recorder(dict(ACTOR), run_id="r1", stage="v3", approvals=True)
    close = SimpleNamespace(
        tool_use={
            "name": "TicketTools___close_ticket",
            "input": {
                "customer_id": "customer-a",
                "ticket_id": "A-42",
                "reason": "operator request",
            },
        },
        cancel_tool=False,
    )
    note = SimpleNamespace(
        tool_use={
            "name": "TicketTools___add_note",
            "input": {"customer_id": "customer-a", "ticket_id": "A-41", "note": "unrequested"},
        },
        cancel_tool=False,
    )
    rec.before(close)
    rec.before(note)
    assert "held for the operator's approval" in close.cancel_tool
    assert "no trusted operator task" in note.cancel_tool
    assert [c.status for c in rec.calls] == ["PENDING_APPROVAL", "NOT_EVALUATED"]
    assert rec.calls[0].input["actor"] == ACTOR and rec.calls[0].input["run_id"] == "r1"


def test_v3_replay_sends_the_close_straight_to_the_gateway_where_policy_decides():
    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    result = replay("v3")
    assert "TicketTools___close_ticket" in [n for n, _ in FakeGateway.instances[0].sent]
    assert result["tool_calls"][1]["decision"] == "DENY"


def test_an_approved_call_is_sent_once_with_no_model_call():
    calls = [
        {
            "tool": "close_ticket",
            "input": {"customer_id": "customer-a", "ticket_id": "A-41", "reason": "approved"},
        }
    ]
    grant = {
        "owner": "r1",
        "user_sub": "sub-alice",
        "session": "ps-1",
        "id": "r1-1",
        "tool": "close_ticket",
        "ticket_id": "A-41",
        "reason": "approved",
    }
    result = replay(
        "v3",
        calls=calls,
        extra={"approval": "r1-1"},
        state_table=StateTable({"busy_stage": "v3", "approval_dispatch": json.dumps(grant)}),
    )
    assert [n for n, _ in FakeGateway.instances[0].sent] == ["TicketTools___close_ticket"]
    assert result["tool_calls"][0]["status"] == "OK"
    assert "approved" in result["answer"]


# ── V4: circuit breaker and kill switch ──────────────────────────────────
def test_v4_first_denial_trips_the_breaker_and_skips_the_rest():
    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    table = StateTable()
    result = replay("v4", screen=screen(Guardrail("NONE")), state_table=table)
    assert result["halted"] is True and result["read_only"] is False
    assert [c["status"] for c in result["tool_calls"]] == ["OK", "ERROR", "NOT_EVALUATED"]
    assert [n for n, _ in FakeGateway.instances[0].sent][-1] == "TicketTools___close_ticket"
    key, values = table.updates[1]
    assert key == {"customer_id": "customer-a"} and values[":t"] is True
    assert "close_ticket" in values[":r"]
    assert {
        "control": "Circuit breaker",
        "status": "OPEN · tripped by close_ticket B-77 denied · agent is read-only",
    } in result["controls"]


def test_v4_trip_persistence_failure_retains_intent_and_recovered_read_only():
    class FailingTrip(StateTable):
        def update_item(
            self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None
        ):
            if "breaker_open = :t" in UpdateExpression:
                raise RuntimeError("write refused")
            return super().update_item(
                Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression
            )

    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    table = FailingTrip()
    first = replay("v4", state_table=table)
    assert first["halted"] and first["containment_uncertain"]
    assert table.item["breaker_pending"] and not table.item.get("breaker_open")
    assert "UNCONFIRMED latch" in first["controls"][-2]["status"]
    FakeGateway.replies = {}
    second = replay("v4", state_table=table)
    assert (
        second["read_only"] and [c["status"] for c in second["tool_calls"]][1:] == ["READ_ONLY"] * 2
    )


def test_v4_ambiguous_trip_commit_is_verified_by_consistent_read():
    class LostTripAck(StateTable):
        def update_item(
            self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None
        ):
            out = super().update_item(
                Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression
            )
            if "breaker_open = :t" in UpdateExpression:
                raise RuntimeError("reply lost after durable OPEN")
            return out

    FakeGateway.replies = {"close_ticket": {"status": "error", "content": [{"text": DENIED}]}}
    table = LostTripAck()
    first = replay("v4", state_table=table)
    assert first["containment_durable"] and not first["containment_uncertain"]
    assert table.item["breaker_open"] and "breaker_pending" not in table.item
    assert replay("v4", state_table=table)["read_only"]


def test_v4_clean_release_receipt_and_failed_release_intent():
    class LostRelease(StateTable):
        def __init__(self, commit):
            super().__init__()
            self.commit = commit

        def update_item(
            self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None
        ):
            if "breaker_receipt" in UpdateExpression:
                if self.commit:
                    super().update_item(
                        Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression
                    )
                raise RuntimeError("lost ack")
            return super().update_item(
                Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression
            )

    for commit in (False, True):
        table = LostRelease(commit)
        result = replay("v4", calls=[], state_table=table)
        assert bool(table.item.get("breaker_pending")) is not commit
        assert table.item.get("breaker_receipt", "").startswith("CLEAN_COMPLETED:") is commit
        assert bool(result["persistence_error"]) is not commit
        next_run = replay("v4", calls=[], state_table=table)
        assert next_run["read_only"] is not commit


def test_v4_clean_release_lost_ack_and_unavailable_verification_recovers_by_receipt():
    class LostReleaseAndRead(StateTable):
        unreadable = False

        def update_item(
            self, Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression=None
        ):
            result = super().update_item(
                Key, UpdateExpression, ExpressionAttributeValues, ConditionExpression
            )
            if "breaker_receipt" in UpdateExpression:
                self.unreadable = True
                raise RuntimeError("reply lost")
            return result

        def get_item(self, Key, ConsistentRead):
            if self.unreadable:
                self.unreadable = False
                raise RuntimeError("verification unavailable")
            return super().get_item(Key, ConsistentRead)

    table = LostReleaseAndRead()
    first = replay("v4", calls=[], state_table=table)
    assert first["persistence_error"] and table.item["breaker_receipt"].startswith(
        "CLEAN_COMPLETED:"
    )
    assert "breaker_pending" not in table.item
    second = replay("v4", calls=[], state_table=table)
    assert not second["read_only"] and not second["refused"]


def test_v4_open_breaker_admits_read_only_and_refuses_every_write():
    table = StateTable({"breaker_open": True, "breaker_reason": "AgentCore Policy denied x"})
    result = replay("v4", state_table=table)
    assert result["refused"] == "" and result["read_only"] is True and result["halted"] is False
    assert [n for n, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    assert [c["status"] for c in result["tool_calls"]] == ["OK", "READ_ONLY", "READ_ONLY"]
    assert table.updates == []  # a refused write is not a new denial: the breaker is not re-tripped
    assert result["controls"][0] == {
        "control": "Circuit breaker",
        "status": "OPEN · admitted read-only · writes refused",
    }


@pytest.mark.parametrize("stage", ["v0", "v1", "v2", "v3", "v4", "v5"])
def test_stop_is_checked_before_runtime_gateway_for_every_build(stage):
    item = {"busy_stage": stage, "stop_requested": True}
    result = replay(stage, state_table=StateTable(item))
    assert "stopped by an operator" in result["refused"]
    assert result["stop_reason"] == "refused" and result["tool_calls"] == []
    assert FakeGateway.instances == []


@pytest.mark.parametrize(
    "table, why",
    [
        (StateTable({"stop_requested": True, "stop_source": "operator"}), "stopped by an operator"),
        (StateTable(fail=True), "policy session state unavailable"),
    ],
)
def test_v4_kill_switch_refuses_at_admission_without_touching_the_gateway(table, why):
    result = replay("v4", state_table=table)
    assert why in result["refused"]
    assert FakeGateway.instances == []
    assert result["tool_calls"] == [] and result["stop_reason"] == "refused"
    assert result["controls"] == [
        {"control": "Kill switch", "status": "REFUSED at admission · agent is stopped"}
    ]


def test_v4_admits_when_no_stop_is_set():
    result = replay("v4", state_table=StateTable())
    assert result["refused"] == "" and len(FakeGateway.instances[0].sent) == 2
    assert result["tool_calls"][2]["status"] == "NOT_EVALUATED"


def test_v4_turn_limit_trips_the_breaker():
    from ticket_agent.agent import Recorder
    from ticket_agent.controls import MAX_TURNS, CircuitBreaker

    table = StateTable()
    rec = Recorder(ACTOR, stage="v4", breaker=CircuitBreaker("customer-a", table))
    assert rec.breaker.admit() is None
    rec.turn = MAX_TURNS
    event = SimpleNamespace(agent=None, cancel=False)
    rec.model_start(event)
    assert event.cancel and rec.breaker.tripped
    assert "turn limit" in table.updates[1][1][":r"]


# ── app.py: per-build controls from the environment ───────────────────────
@pytest.fixture
def app(monkeypatch):
    runtime = ModuleType("bedrock_agentcore.runtime")
    runtime.BedrockAgentCoreApp = lambda: SimpleNamespace(entrypoint=lambda f: f, run=None)
    boto3 = ModuleType("boto3")
    boto3.resource = lambda name: SimpleNamespace(Table=lambda t: ("table", name, t))
    boto3.client = lambda name: ("client", name)
    monkeypatch.setitem(sys.modules, "bedrock_agentcore", ModuleType("bedrock_agentcore"))
    monkeypatch.setitem(sys.modules, "bedrock_agentcore.runtime", runtime)
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setenv(
        "GATEWAY_URLS", json.dumps({k: f"https://{k}" for k in ("v0", "v1", "v3", "v4")})
    )
    monkeypatch.setenv("GUARDRAIL_ID", "gr-1")
    monkeypatch.setenv("GUARDRAIL_VERSION", "2")
    monkeypatch.setenv("STATE_TABLE", "state")
    monkeypatch.setenv("MODEL_ID", "m")
    monkeypatch.delitem(sys.modules, "ticket_agent.app", raising=False)
    return importlib.import_module("ticket_agent.app")


@pytest.mark.parametrize(
    "stage, url, screened, contained",
    [
        ("v0", "https://v0", False, True),
        ("v1", "https://v0", True, True),
        ("v2", "https://v1", True, True),
        ("v3", "https://v3", True, True),
        ("v4", "https://v4", True, True),
        ("v5", "https://v4", True, True),
    ],
)
def test_build_controls_follow_the_build(app, stage, url, screened, contained):
    c = app.build_controls(stage)
    assert c["gateway_url"] == url
    assert ("screen" in c) is screened and ("state_table" in c) is contained
    if screened:
        assert (c["screen"].guardrail_id, c["screen"].version) == ("gr-1", "2")
    if contained:
        assert c["state_table"] == ("table", "dynamodb", "state")


def test_unconfigured_control_fails_the_run_instead_of_running_without_it(app, monkeypatch):
    monkeypatch.delenv("GUARDRAIL_ID")
    assert app.build_controls("v0")["gateway_url"] == "https://v0"
    with pytest.raises(KeyError):
        app.build_controls("v1")
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    assert "GUARDRAIL_ID" in app.invoke({"stage": "v1"}, context)["error"]


def test_invoke_runs_the_payload_build(app, monkeypatch):
    seen = {}

    def fake_run(payload, token, **kw):
        seen.update(kw, payload=payload, token=token)
        return {
            "run_id": "r1",
            "stage": payload["stage"],
            "mode": "live",
            "tool_calls": [],
            "halted": False,
            "blocked": False,
            "refused": "",
        }

    monkeypatch.setattr(app, "run", fake_run)
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    app.invoke({"stage": "v4"}, context)
    assert seen["gateway_url"] == "https://v4" and seen["token"] == TOKEN
    assert "screen" in seen and seen["state_table"] == ("table", "dynamodb", "state")
    seen.clear()
    assert app.invoke({"stage": "v9"}, context)["error"] == "explicit valid build required"
    assert seen == {}  # no silent downgrade at Runtime ingress


# ── V5: cumulative enforcement at the deployed Runtime entrypoint ─────────
def test_v5_entrypoint_clean_denial_retains_identity_guardrail_policy_and_breaker(app, monkeypatch):
    """Invoke the real app.invoke → run → Recorder replay path, without AWS/model calls."""
    import ticket_agent.events as event_module

    seen = []
    original = event_module.Events.emit

    def capture(self, *args, **kwargs):
        item = original(self, *args, **kwargs)
        seen.append(item)
        return item

    monkeypatch.setattr(event_module.Events, "emit", capture)
    table = StateTable({"busy_stage": "v5"})
    monkeypatch.setattr(
        sys.modules["boto3"], "resource", lambda name: SimpleNamespace(Table=lambda t: table)
    )
    guard = Guardrail("NONE")
    monkeypatch.setattr(app, "Screen", lambda identifier, version: screen(guard))
    FakeGateway.replies = {
        "list_tickets": {"status": "success", "content": [{"text": TICKETS}]},
        "close_ticket": {"status": "error", "content": [{"text": DENIED}]},
    }
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    payload = {
        "stage": "v5",
        "mode": "replay",
        "calls": CAPTURE,
        "run_id": "r1",
        "policy_session": "ps-1",
    }
    result = app.invoke(payload, context)
    gw = FakeGateway.instances[0]
    assert gw.url == "https://v4"  # V5 uses the cumulative V4 ENFORCE Gateway
    assert gw.headers["x-amzn-bedrock-agentcore-policy-session-id"] == "ps-1"
    assert [name for name, _ in gw.sent] == [
        "TicketTools___list_tickets",
        "TicketTools___close_ticket",
    ]
    assert guard.seen == [TICKETS]
    assert [call["decision"] for call in result["tool_calls"]] == ["ALLOW", "DENY", "NOT_EVALUATED"]
    assert result["halted"] and table.item["breaker_open"]
    kinds = [e["kind"] for e in seen]
    for kind in (
        "identity.validated",
        "runtime.entered",
        "guardrail.checked",
        "policy.decision",
        "run.halted",
    ):
        assert kind in kinds
    assert [(e["tool"], e["decision"]) for e in seen if e["kind"] == "policy.decision"] == [
        ("list_tickets", "ALLOW"),
        ("close_ticket", "DENY"),
    ]


def test_v5_entrypoint_open_breaker_preserves_read_allow_and_never_dispatches_writes(
    app, monkeypatch
):
    table = StateTable({"busy_stage": "v5", "breaker_open": True})
    monkeypatch.setattr(
        sys.modules["boto3"], "resource", lambda name: SimpleNamespace(Table=lambda t: table)
    )
    guard = Guardrail("NONE")
    monkeypatch.setattr(app, "Screen", lambda identifier, version: screen(guard))
    FakeGateway.replies = {"list_tickets": {"status": "success", "content": [{"text": TICKETS}]}}
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    result = app.invoke(
        {
            "stage": "v5",
            "mode": "replay",
            "calls": CAPTURE,
            "run_id": "r1",
            "policy_session": "ps-1",
        },
        context,
    )
    assert [name for name, _ in FakeGateway.instances[0].sent] == ["TicketTools___list_tickets"]
    assert [c["decision"] for c in result["tool_calls"]] == ["ALLOW", "READ_ONLY", "READ_ONLY"]
    assert result["read_only"] and not result["halted"] and not table.updates
    assert guard.seen == [TICKETS]


def test_v5_entrypoint_live_close_is_held_before_gateway(app, monkeypatch):
    """Stub the model turn only; real entrypoint/Recorder must hold an unapproved close."""
    import ticket_agent.agent as agent_module

    table = StateTable({"busy_stage": "v5"})
    monkeypatch.setattr(
        sys.modules["boto3"], "resource", lambda name: SimpleNamespace(Table=lambda t: table)
    )
    monkeypatch.setattr(app, "Screen", lambda identifier, version: screen(Guardrail()))
    held = []

    def fake_live(payload, actor, gateway, recorder, model_id, run_id):
        event = agent_module.ReplayToolEvent(
            {
                "toolUseId": "hold-1",
                "name": "TicketTools___close_ticket",
                "input": {
                    "customer_id": "customer-a",
                    "ticket_id": "A-41",
                    "reason": "operator request",
                },
            }
        )
        recorder.before(event)
        held.append(event.cancel_tool)
        return "", "model stubbed"

    monkeypatch.setattr(agent_module, "live", fake_live)
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    result = app.invoke(
        {"stage": "v5", "mode": "live", "run_id": "r1", "policy_session": "ps-1"}, context
    )
    assert "held for the operator's approval" in held[0]
    assert [c["status"] for c in result["tool_calls"]] == ["PENDING_APPROVAL"]
    assert not FakeGateway.instances[0].sent
