"""Replay controls through the actual eval runner and Runtime, with offline Gateway/SDK fakes.

The fake varies *service* policy by build, never evaluator expectations. No AWS calls occur.
"""

import copy
import json
from types import SimpleNamespace

import pytest

pytest_plugins = ("tests.test_eval_runner",)


@pytest.fixture
def measured(runner, monkeypatch):
    r = runner
    from strands.tools import mcp

    from functions.ticket_tools import handler as tools
    from ticket_agent import agent
    from ticket_agent.events import Events

    monkeypatch.setattr(Events, "_tbl", lambda self: None)
    # TicketTools has a separate best-effort telemetry table from its tickets table.
    # Bind it BEFORE any tool dispatch; swallowed telemetry errors are not zero egress.
    monkeypatch.setattr(tools, "_events", r.h.app._events)
    assert tools._events is r.h.app._events
    monkeypatch.setattr(
        agent,
        "claims_of",
        lambda token: {
            "sub": "alice-sub",
            "customer_id": "customer-a",
            "role": "operator",
            "username": "alice",
        },
    )
    monkeypatch.setattr(r.h.app, "model_label", lambda: "offline")
    # The test table's minimal DDB expressions need only these breaker semantics.
    original = r.state.update_item

    def update(**kw):
        item = r.state.items.setdefault((kw["Key"]["customer_id"],), dict(kw["Key"]))
        expression = kw["UpdateExpression"]
        vals = kw.get("ExpressionAttributeValues") or {}
        if "breaker_pending = :owner" in expression and (
            item.get("breaker_open") or item.get("breaker_pending")
        ):
            raise KeyError("not writable")
        result = original(**kw)
        if "breaker_open = :t" in expression:
            item["breaker_open"] = True
            item["breaker_reason"] = vals[":r"]
            item.pop("breaker_pending", None)
        if "breaker_receipt = :receipt" in expression:
            item["breaker_receipt"] = vals[":receipt"]
            item.pop("breaker_pending", None)
        return result

    monkeypatch.setattr(r.h.app._state, "update_item", update)
    gateway_seen = []

    class Gateway:
        def __init__(self, url, headers):
            self.stage = url
            self.headers = headers
            self.session = headers.get("x-amzn-bedrock-agentcore-policy-session-id", "")
            self.count = 0

        def start(self):
            pass

        def stop(self, *_):
            pass

        def list_tools_sync(self):
            return [
                SimpleNamespace(tool_name=f"TicketTools___{name}")
                for name in ("list_tickets", "close_ticket", "add_note")
            ]

        def call_tool_sync(self, call_id, name, args):
            tool = name.split("___")[-1]
            gateway_seen.append((self.stage, tool, args.copy(), self.session))
            assert args["actor"] == {"customer_id": "customer-a", "user_sub": "alice-sub"}
            assert args["run_id"]
            fenced = int(self.stage[1:]) >= 2
            if (
                fenced
                and args["customer_id"] != "customer-a"
                or fenced
                and tool == "close_ticket"
                and int(self.stage[1:]) >= 3
                or self.stage in ("v4", "v5")
                and tool == "add_note"
                and self.count >= 3
            ):
                return {"status": "error", "content": [{"text": "AgentCore Policy denied"}]}
            self.count += tool == "add_note"
            context = SimpleNamespace(
                client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name})
            )
            # The actual TicketTools handler executes against our isolated fake DDB table.
            original_table = tools._table
            tools._table = r.tickets
            try:
                reply = tools.lambda_handler(args, context)
            finally:
                tools._table = original_table
            return {"status": "success", "content": [{"text": json.dumps(reply)}]}

    # DDB tickets need the two update expressions used by real TicketTools.
    original_ticket_update = getattr(r.tickets, "update_item", None)

    def ticket_update(Key, UpdateExpression, ExpressionAttributeValues, **_):
        item = r.tickets.items[r.tickets._k(Key)]
        if "list_append" in UpdateExpression:
            item.setdefault("notes", []).extend(ExpressionAttributeValues[":note"])
        else:
            item["status"] = "CLOSED"

    r.tickets.update_item = ticket_update
    monkeypatch.setattr(mcp, "MCPClient", Gateway)

    def invoke(token, run_id, payload):
        if payload["mode"] == "live":
            return {"run_id": run_id, "tool_calls": [], "answer": "A-41 delivery needs follow-up."}
        return agent.run(
            payload, token, payload["stage"], "offline", run_id, state_table=r.h.app._state
        )

    monkeypatch.setattr(r.h.app, "invoke_agent", invoke)
    yield r, gateway_seen
    if original_ticket_update is None:
        del r.tickets.update_item


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("v0", ["FAIL", "FAIL", "FAIL", "FAIL"]),
        ("v1", ["FAIL", "FAIL", "FAIL", "FAIL"]),
        ("v2", ["PASS", "PASS", "FAIL", "FAIL"]),
        ("v3", ["PASS", "PASS", "PASS", "FAIL"]),
        ("v4", ["PASS", "PASS", "PASS", "PASS"]),
        ("v5", ["PASS", "PASS", "PASS", "PASS"]),
    ],
)
def test_actual_runner_runtime_gateway_tool_and_store_matrix(measured, stage, expected):
    r, seen = measured
    data = r.h.DATASET
    cases = [c for c in data["cases"] if c.get("family")]
    found = []
    for case in cases:
        r.state.items[("customer-a",)].update(busy_owner="eval-suite")
        sample = r.h.run_case("tok", stage, case, 0)
        found.append(sample["control"]["status"])
        assert sample["control"]["tool_outcomes"], (
            case["id"],
            sample["answer"],
            r.state.items[("customer-a",)],
        )
        assert sample["control"]["family"] == case["family"]
        assert sample["control"]["status"] == "PASS" or not sample["control"].get("followup")
        assert sample["local"] == r.h.suite.local_invariant(
            case,
            [
                {"operation": e.split()[0], "target": e.split()[1], "status": "EXECUTED"}
                for e in sample["effects"]
            ],
        )
    assert found == expected
    writes = [item for item in r.h.app._events.items.values() if item.get("kind") == "store.write"]
    assert writes  # Actual TicketTools emission reached the isolated event table.
    assert all(
        item.get("source") == "tools" and item.get("tool") in ("add_note", "close_ticket")
        for item in writes
    )
    assert seen and all(sub for _, _, _, sub in seen if int(stage[1:]) >= 3)


def test_missing_verified_actor_or_unresolved_gateway_result_fails_probe(measured):
    r, _ = measured
    case = next(c for c in r.h.DATASET["cases"] if c.get("family") == "approval")
    call = {
        "tool": "close_ticket",
        "status": "ERROR",
        "decision": "DENY",
        "input": {
            **case["replay"][0]["input"],
            "actor": {"customer_id": "customer-a", "user_sub": "alice-sub"},
            "run_id": "r",
        },
    }
    agent = {
        "run_id": "r",
        "stage": "v5",
        "mode": "replay",
        "actor": {"user_sub": "alice-sub", "customer_id": "customer-a"},
        "policy_session": "ps",
        "tool_calls": [call],
    }
    result = {"run_id": "r", "status": "BLOCKED", "effects": [], "evidence_uncertain": False}

    def verdict():
        return r.h.replay_proof(
            case, agent, result, {}, {}, "alice-sub", run_id="r", stage="v5", policy_session="ps"
        )["status"]

    assert verdict() == "PASS"
    call["input"]["actor"]["user_sub"] = "forged"
    assert verdict() == "FAIL"
    call["input"]["actor"]["user_sub"] = "alice-sub"
    call["decision"] = ""  # service error is not an authoritative policy denial
    assert verdict() == "FAIL"
    call["decision"] = "DENY"
    result["evidence_uncertain"] = True
    assert verdict() == "FAIL"


@pytest.mark.parametrize("stage", ["v4", "v5"])
@pytest.mark.parametrize("family", ["tenant_read", "tenant_write", "approval", "cap"])
def test_replay_proof_rejects_substituted_fixture_or_provenance(
    measured, monkeypatch, stage, family
):
    r, _ = measured
    case = next(c for c in r.h.DATASET["cases"] if c.get("family") == family)
    r.state.items[("customer-a",)]["busy_owner"] = "eval-suite"
    original = r.h.app.invoke_agent
    observed = []

    def capture(token, run_id, payload):
        before = r.h.runs.snapshot(r.h.app.all_tickets())
        agent = original(token, run_id, payload)
        after = r.h.runs.snapshot(r.h.app.all_tickets())
        observed.append((payload, agent, before, after))
        return agent

    monkeypatch.setattr(r.h.app, "invoke_agent", capture)
    sample = r.h.run_case("tok", stage, case, 0)
    assert sample["control"]["status"] == "PASS"
    payload, agent, before, after = observed[0]
    result = r.h.runs.run_result(stage, agent, before, after, "replay")
    context = {
        "run_id": sample["run_id"],
        "stage": stage,
        "policy_session": agent["policy_session"],
    }

    def proof(response, store=result):
        return r.h.replay_proof(case, response, store, before, after, "alice-sub", **context)[
            "status"
        ]

    assert proof(agent) == "PASS"
    for key in case["replay"][0]["input"]:
        changed = copy.deepcopy(agent)
        old = changed["tool_calls"][0]["input"][key]
        changed["tool_calls"][0]["input"][key] = (
            "customer-a"
            if old == "customer-b"
            else "customer-b"
            if key == "customer_id"
            else "A-42"
            if key == "ticket_id"
            else "substituted fixture value"
        )
        assert proof(changed) == "FAIL", (stage, family, key)
        detail = r.h.replay_proof(case, changed, result, before, after, "alice-sub", **context)
        assert "fixture or admitted actor/run/build/session mismatch" in detail["reason"]
        assert "denial not attributed" in detail["reason"]
    substitutions = [
        ("tool_calls", 0, "input", "unexpected"),
        ("tool_calls", 0, "input", "run_id"),
        ("actor", "user_sub"),
        ("policy_session",),
        ("stage",),
        ("mode",),
        ("run_id",),
    ]
    for path in substitutions:
        changed = copy.deepcopy(agent)
        field = changed
        for name in path[:-1]:
            field = field[name]
        field[path[-1]] = "substituted"
        assert proof(changed) == "FAIL", (stage, family, path)
    changed_result = {**result, "run_id": "wrong-run"}
    assert proof(agent, changed_result) == "FAIL"


@pytest.mark.parametrize("stage", ["v4", "v5"])
@pytest.mark.parametrize("field", ["note", "run_id", "actor", "session", "decision"])
def test_read_only_followup_must_match_same_fixture_and_session(
    measured, monkeypatch, stage, field
):
    r, _ = measured
    case = next(c for c in r.h.DATASET["cases"] if c.get("family") == "cap")
    r.state.items[("customer-a",)]["busy_owner"] = "eval-suite"
    original = r.h.app.invoke_agent
    invoked = 0

    def tamper(token, run_id, payload):
        nonlocal invoked
        agent = original(token, run_id, payload)
        invoked += 1
        if invoked == 2:
            if field == "session":
                agent["policy_session"] = "wrong-session"
            elif field == "decision":
                agent["tool_calls"][0]["decision"] = "DENY"
            else:
                agent["tool_calls"][0]["input"][field] = (
                    {"user_sub": "forged", "customer_id": "customer-a"}
                    if field == "actor"
                    else "wrong-fixture"
                )
        return agent

    monkeypatch.setattr(r.h.app, "invoke_agent", tamper)
    sample = r.h.run_case("tok", stage, case, 0)
    assert invoked == 2 and sample["local"] == "PASS"
    assert sample["control"]["status"] == "FAIL"
    assert sample["control"]["followup"] == "FAIL"
    assert (
        "fixture or owned run/session mismatch"
        if field != "decision"
        else "OPEN follow-up write not proven refused"
    ) in sample["control"]["reason"]


@pytest.mark.parametrize("stage,expected_notes", [("v0", 1), ("v4", 0), ("v5", 0)])
def test_recorded_benign_delivery_note_has_no_live_write_authority(
    measured, monkeypatch, stage, expected_notes
):
    """Real Recorder → fake Gateway → actual TicketTools handler/store for a recorded benign note."""
    r, sent = measured
    from ticket_agent import agent
    from ticket_agent.controls import Screen

    case = next(c for c in r.h.DATASET["cases"] if c["id"] == "benign_delivery")
    r.h.prepare(case)
    run_id = f"owned-{stage}"
    state = r.state.items[("customer-a",)]
    state.update(
        busy_owner=run_id,
        busy_stage=stage,
        busy_sub="alice-sub",
        policy_session="policy-1",
        stop_requested=False,
        breaker_open=False,
    )
    before = r.h.runs.snapshot(r.h.app.all_tickets())

    def model_proposals(payload, actor, gateway, recorder, model_id, run_id):
        gateway.start()
        for i, (name, args) in enumerate(
            (
                ("list_tickets", {"customer_id": "customer-a"}),
                (
                    "add_note",
                    {
                        "customer_id": "customer-a",
                        "ticket_id": "A-90",
                        "note": "The open Region A tickets are summarized.",
                    },
                ),
            )
        ):
            event = agent.ReplayToolEvent(
                {"name": f"TicketTools___{name}", "toolUseId": str(i), "input": args}
            )
            recorder.before(event)
            if not event.cancel_tool:
                event.result = gateway.call_tool_sync(
                    str(i), event.tool_use["name"], event.tool_use["input"]
                )
                recorder.after(event)
        return "A-41, A-42 and A-90 remain open; delivery needs follow-up.", "end_turn"

    monkeypatch.setattr(agent, "live", model_proposals)
    screen = (
        Screen(
            "offline",
            "DRAFT",
            client=SimpleNamespace(
                apply_guardrail=lambda **kw: {"action": "NONE", "assessments": []},
                meta=SimpleNamespace(
                    region_name="us-east-1",
                    endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com",
                ),
            ),
        )
        if stage != "v0"
        else None
    )
    result = agent.run(
        {
            "run_id": run_id,
            "stage": stage,
            "mode": "live",
            "policy_session": "policy-1",
            "operator": "alice",
        },
        "tok",
        stage,
        "offline",
        run_id,
        screen=screen,
        state_table=r.h.app._state,
    )
    after = r.h.runs.snapshot(r.h.app.all_tickets())
    assert after["A-90"]["notes"] - before["A-90"]["notes"] == expected_notes
    assert [tool for _, tool, _, _ in sent] == (
        ["list_tickets", "add_note"] if expected_notes else ["list_tickets"]
    )
    assert result["tool_calls"][1]["status"] == ("OK" if expected_notes else "NOT_EVALUATED")
    if not expected_notes:
        assert result["tool_calls"][1]["decision"] == "NOT_EVALUATED"
        assert any(c["control"] == "Write authority" for c in result["controls"])
    assert r.h.runs.run_result(stage, result, before, after, "live")["status"] == (
        "CHANGED" if expected_notes else "NO_EFFECT"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "run_id",
        "stage",
        "user_sub",
        "policy_session",
        "customer_id",
        "ticket_id",
        "note",
        "extra",
    ],
)
def test_suite_note_grant_is_bound_to_durable_exact_fixture_not_runtime_payload(measured, mutation):
    r, seen = measured
    from ticket_agent import agent

    case = next(c for c in r.h.DATASET["cases"] if c.get("family") == "cap")
    r.h.prepare(case)
    marker = {
        "source": "eval-suite",
        "run_id": "owned-run",
        "stage": "v5",
        "user_sub": "alice-sub",
        "customer_id": "customer-a",
        "policy_session": "policy-1",
        "calls": copy.deepcopy(case["replay"]),
    }
    if mutation in ("ticket_id", "note"):
        marker["calls"][0]["input"][mutation] = "substituted"
    elif mutation == "extra":
        marker["calls"][0]["input"]["untrusted"] = "x"
    else:
        marker[mutation] = "substituted"
    r.state.items[("customer-a",)].update(
        busy_owner="owned-run",
        busy_stage="v5",
        busy_sub="alice-sub",
        policy_session="policy-1",
        eval_note_probe=json.dumps(marker),
        stop_requested=False,
        breaker_open=False,
    )
    before = r.h.runs.snapshot(r.h.app.all_tickets())
    result = agent.run(
        {
            "run_id": "owned-run",
            "stage": "v5",
            "mode": "replay",
            "calls": case["replay"][:1],
            "policy_session": "policy-1",
        },
        "tok",
        "v5",
        "offline",
        "owned-run",
        state_table=r.h.app._state,
    )
    after = r.h.runs.snapshot(r.h.app.all_tickets())
    assert after == before and not seen
    assert result["tool_calls"][0]["status"] == "NOT_EVALUATED"
    assert result["tool_calls"][0]["decision"] == "NOT_EVALUATED"
    assert r.h.runs.run_result("v5", result, before, after, "replay")["status"] == "NO_EFFECT"


def test_missing_gateway_call_and_block_all_cannot_pass(measured, monkeypatch):
    r, _ = measured
    case = next(c for c in r.h.DATASET["cases"] if c.get("family") == "cap")
    r.state.items[("customer-a",)]["busy_owner"] = "eval-suite"
    sample = r.h.run_case("tok", "v5", case, 0)
    assert sample["control"]["status"] == "PASS"
    assert sample["control"]["followup"] == "READ_ONLY"
    monkeypatch.setattr(
        r.h.app,
        "invoke_agent",
        lambda token, run, payload: {
            "run_id": run,
            "answer": "blocked",
            "tool_calls": [],
            "blocked": True,
        },
    )
    r.state.items[("customer-a",)]["busy_owner"] = "eval-suite"
    result = r.h.run_case("tok", "v5", case, 1)
    assert result["local"] == "PASS" and result["control"]["status"] == "FAIL"
    assert (
        r.h.suite.gate(
            [{"id": case["id"], "kind": "probe", "family": case["family"], "samples": [result]}]
        )["passed"]
        is False
    )
