"""Actual installed MCPClient streamable HTTP + Strands + BedrockModel, loopback only."""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.types import Tool

from functions.ticket_tools import handler as tools
from tests.test_tool_projection import ADDITIONAL_CASES, ADDITIONAL_IDS, additional_schema
from tests.test_tool_projection_integration import ACTOR, Bedrock, Guardrail, State
from ticket_agent import agent as runtime

pytest_plugins = ("tests.test_security_regressions",)

RAW = json.loads((Path(__file__).parent / "fixtures/gateway_tools_v3.json").read_text())["tools"]


class Tickets:
    def __init__(self):
        self.rows = [
            {
                "customer_id": "customer-a",
                "ticket_id": "A-41",
                "subject": "Delivery delayed",
                "body": "Please explain the delay.",
                "status": "OPEN",
                "origin": "seed",
            }
        ]

    def query(self, KeyConditionExpression, ExpressionAttributeValues):
        return {
            "Items": [
                dict(row)
                for row in self.rows
                if row["customer_id"] == ExpressionAttributeValues[":c"]
            ]
        }


class Events:
    def __init__(self):
        self.items = []

    def put_item(self, Item):
        self.items.append(dict(Item))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, status, body=None):
        encoded = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def do_GET(self):
        self.reply(405)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        assert 0 < length < 65536
        request = json.loads(self.rfile.read(length))
        assert self.path == "/mcp"
        assert self.headers.get("Authorization") == f"Bearer {self.server.auth_token}"
        assert (
            self.headers.get("x-amzn-bedrock-agentcore-policy-session-id")
            == self.server.expected_policy_session
        )
        method = request["method"]
        self.server.received.append((method, request, dict(self.headers)))
        if method == "notifications/initialized":
            assert "id" not in request
            self.reply(202)
            return
        if method in ("rpc.discover", "discover"):
            self.reply(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": "Method not found"},
                },
            )
            return
        if method == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "local MCP Gateway", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    Tool(**row).model_dump(by_alias=True, exclude_none=True)
                    for row in self.server.raw_tools
                ]
            }
        elif method == "tools/call":
            name, args = request["params"]["name"], request["params"]["arguments"]
            assert name in (
                "TicketTools___list_tickets",
                "TicketTools___close_ticket",
                "TicketTools___confirm_close",
            )
            assert args["actor"] == ACTOR and args["customer_id"] == "customer-a"
            if name == "TicketTools___confirm_close":
                assert args == {
                    "customer_id": "customer-a",
                    "ticket_id": self.server.expected_ticket,
                    "actor": ACTOR,
                }
            else:
                assert args["run_id"] == self.server.expected_run
            if name == "TicketTools___close_ticket":
                assert args["ticket_id"] == self.server.expected_ticket
                assert self.server.admission_state is not None
                assert self.server.admission_state.get_item(
                    Key={"customer_id": "customer-a"}, ConsistentRead=True
                )["Item"].get("approval_dispatch")
            context = SimpleNamespace(
                client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name})
            )
            native = tools.lambda_handler(args, context)
            if name == "TicketTools___confirm_close" and self.server.confirm_override:
                native = self.server.confirm_override(native)
            result = {
                "content": [{"type": "text", "text": json.dumps(native)}],
                "isError": False,
            }
        else:
            self.reply(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "error": {"code": -32601, "message": "Method not found"},
                },
            )
            return
        self.reply(200, {"jsonrpc": "2.0", "id": request["id"], "result": result})


@pytest.fixture
def local_mcp(monkeypatch):
    from strands import models
    from strands.models.bedrock import BedrockModel as RealBedrockModel
    from strands.tools import mcp
    from strands.tools.mcp.mcp_client import MCPClient as RealMCPClient

    from ticket_agent.events import Events as RuntimeEvents

    # Pin the installed public implementations even after older replay-only fake
    # fixtures monkeypatch their module attributes during the same pytest process.
    monkeypatch.setattr(mcp, "MCPClient", RealMCPClient)
    monkeypatch.setattr(models, "BedrockModel", RealBedrockModel)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.received = []
    server.raw_tools = copy.deepcopy(RAW)
    server.auth_token, server.expected_run, server.admission_state = "local", "owned-run", None
    server.expected_ticket = "A-41"
    server.confirm_override = None
    server.expected_policy_session = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("EVENTS_TABLE", "offline-events")
    tickets, events = Tickets(), Events()
    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: events)
    monkeypatch.setattr(tools, "_table", tickets)
    monkeypatch.setattr(tools, "_events", events)
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp", server, tickets, events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("scope,label,bad", ADDITIONAL_CASES, ids=ADDITIONAL_IDS)
def test_initialized_mcp_rejects_unsupported_raw_schema_before_model_or_effect(
    scope, label, bad, local_mcp, monkeypatch
):
    import strands.models.bedrock as bedrock_module
    from strands.tools.mcp.mcp_client import MCPClient as RealMCPClient

    url, server, tickets, events = local_mcp
    additional_schema(server.raw_tools, scope)["additionalProperties"] = bad
    model = Bedrock()
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    stops = []
    real_stop = RealMCPClient.stop

    def recorded_stop(self, *args):
        stops.append(self)
        return real_stop(self, *args)

    monkeypatch.setattr(RealMCPClient, "stop", recorded_stop)
    with pytest.raises(ValueError, match="Gateway|actor"):
        runtime.run(
            {"run_id": "owned-run", "stage": "v1", "mode": "live", "operator": "alice"},
            "local",
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            "owned-run",
            screen=Guardrail(),
            state_table=State("v1"),
        )
    methods = [name for name, _, _ in server.received]
    assert "initialize" in methods and "tools/list" in methods
    assert methods.index("initialize") < methods.index("tools/list")
    assert "tools/call" not in methods and not model.requests and len(stops) == 1
    assert tickets.rows[0]["status"] == "OPEN"
    assert not any(event.get("kind") in ("store.read", "store.write") for event in events.items)


@pytest.mark.parametrize("fixture_name", ("model_schema_text_v3.json", "model_text_only_r8.json"))
def test_model_schema_json_text_is_not_a_read_or_pending_approval(
    api, local_mcp, monkeypatch, fixture_name
):
    import strands.models.bedrock as bedrock_module

    from tests.test_security_regressions import request
    from ticket_agent.events import Events as RuntimeEvents

    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: api.tables["events"])

    model = Bedrock()
    captured = json.loads((Path(__file__).parent / "fixtures" / fixture_name).read_text())["text"]
    model.schema_text = captured
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    monkeypatch.setattr(
        api.app,
        "invoke_agent",
        lambda token, run_id, payload: runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            state_table=api.tables["state"],
            screen=Guardrail(),
        ),
    )
    status, data = request(api, "/api/run", {"requests": ["A-41"]})
    assert status == 200 and data["result"]["status"] == "NO_EFFECT"
    assert data["result"]["tool_calls"] == [] and data["result"]["effects"] == []
    assert "Operator close request incomplete" in data["result"]["note"]
    assert "the model made no native tool call" in data["result"]["note"]
    assert len(model.requests) == 1
    assert data["result"]["answer"] == captured
    assert not api.app.current_approvals(api.app.demo_state("customer-a"))
    assert not any(name == "tools/call" for name, _, _ in server.received)
    run_id = data["result"]["run_id"]
    ends = [
        entry
        for entry in api.tables["events"].rows.values()
        if entry.get("run_id") == run_id and entry.get("kind") == "model.call_end"
    ]
    assert len(ends) == 1 and ends[0]["label"] == (
        "wrote tool schemas as text (no toolUse or ticket read)"
    )
    completed = [
        entry
        for entry in api.tables["events"].rows.values()
        if entry.get("run_id") == run_id and entry.get("kind") == "run.completed"
    ]
    assert len(completed) == 1 and completed[0]["status"] == "error"
    assert completed[0]["verdict"] == "NO_EFFECT"
    assert (
        completed[0]["label"]
        == "operator close request incomplete · no held close or approval card"
    )


def test_other_ticket_held_card_does_not_complete_verified_ask_or_trigger_repair(
    api, local_mcp, monkeypatch
):
    import strands.models.bedrock as bedrock_module

    from tests.test_security_regressions import request
    from ticket_agent.events import Events as RuntimeEvents

    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: api.tables["events"])
    model = Bedrock()
    model.request_close = True
    model.close_target = "A-42"
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])

    def invoke(token, run_id, payload):
        server.expected_run = run_id
        return runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            screen=Guardrail(),
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, out = request(api, "/api/run", {"requests": ["A-41"]})
    result = out["result"]
    assert status == 200 and result["status"] == "AWAITING_APPROVAL"
    assert "other pending cards are separate" in result["note"]
    assert len(model.requests) == 3 and not result["completion_repair_used"]
    cards = api.app.current_approvals(api.app.demo_state("customer-a"))
    assert len(cards) == 1 and cards[0]["ticket_id"] == "A-42"
    assert cards[0]["requested_by"] == "ticket text"
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
    assert api.tables["tickets"].rows[("customer-a", "A-42")]["status"] == "OPEN"
    assert [row["params"]["name"] for name, row, _ in server.received if name == "tools/call"] == [
        "TicketTools___list_tickets"
    ]


def test_ask_one_repair_exhausted_without_native_close_has_no_card_or_effect(
    api, local_mcp, monkeypatch
):
    import strands.models.bedrock as bedrock_module

    from tests.test_security_regressions import request
    from tests.test_tool_projection_integration import PRINT_AFTER_READ
    from ticket_agent.events import Events as RuntimeEvents

    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: api.tables["events"])
    model = Bedrock()
    model.text_after_read = model.repair_text = True
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])

    def invoke(token, run_id, payload):
        server.expected_run = run_id
        return runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            screen=Guardrail(),
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, data = request(api, "/api/run", {"requests": ["A-41"]})
    result = data["result"]
    assert status == 200 and result["status"] == "NO_EFFECT"
    assert result["completion_repair_used"] and result["completion_repair_exhausted"]
    assert "One owned-conversation native-close completion turn was exhausted" in result["note"]
    assert "Operator close request incomplete" in result["note"]
    assert len(model.requests) == 3
    assert PRINT_AFTER_READ in str(model.requests[2]["messages"][-2])
    assert result["answer"].startswith("Operator close request incomplete")
    assert [c["tool"] for c in result["tool_calls"]] == ["list_tickets"]
    assert not result["effects"] and not api.app.current_approvals(api.app.demo_state("customer-a"))
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
    assert not api.app.demo_state("customer-a").get("busy_owner")
    methods = [name for name, _, _ in server.received]
    assert methods.count("tools/call") == 1
    events = [e for e in api.tables["events"].rows.values() if e.get("run_id") == result["run_id"]]
    assert sum(e.get("kind") == "model.completion_repair" for e in events) == 1
    assert sum(e.get("kind") == "model.task_incomplete" for e in events) == 2
    assert not any(e.get("kind") == "store.write" for e in events)


@pytest.mark.parametrize("late_native", (False, True), ids=("first_native", "native_after_text"))
@pytest.mark.parametrize("explicit_false", (False, True), ids=("captured_absent", "boolean_false"))
def test_trusted_api_ask_to_native_held_card_then_initialized_approval_and_exact_store(
    api, local_mcp, monkeypatch, explicit_false, late_native
):
    import strands.models.bedrock as bedrock_module

    from tests.test_mcp_grant import MCPWire
    from tests.test_security_regressions import request
    from ticket_agent.events import Events as RuntimeEvents

    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: api.tables["events"])
    model = Bedrock()
    model.request_close = not late_native
    model.text_after_read = late_native
    model.repair_close = late_native
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, _, _ = local_mcp
    if explicit_false:
        for row in server.raw_tools:
            row["inputSchema"]["additionalProperties"] = False
            row["inputSchema"]["properties"]["actor"]["additionalProperties"] = False
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    server.admission_state = api.tables["state"]
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])

    def invoke(token, run_id, payload):
        server.expected_run = run_id
        return runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            screen=Guardrail() if payload["mode"] == "live" else None,
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, held = request(api, "/api/run", {"requests": ["A-41"]})
    assert status == 200 and held["result"]["status"] == "AWAITING_APPROVAL"
    first_request = model.requests[0]
    assert "operator asks you to close A-41" in first_request["messages"][0]["content"][0]["text"]
    assert "native TicketTools___close_ticket" in first_request["messages"][0]["content"][0]["text"]
    assert "TicketTools___close_ticket" in first_request["system"][0]["text"]
    assert "TicketTools___list_tickets" in first_request["system"][0]["text"]
    assert len(model.requests) == 3
    assert held["result"]["completion_repair_used"] is late_native
    if late_native:
        assert (
            model.requests[2]["requestMetadata"]
            == first_request["requestMetadata"]
            == {"ticketops_run": held["result"]["run_id"]}
        )
        assert model.requests[2]["toolConfig"]["toolChoice"] == {"auto": {}}
        history = model.requests[2]["messages"]
        assert len(history) >= 5 and 'print("Customer-a' in str(history[-2])
        assert "TicketTools___close_ticket" in history[-1]["content"][0]["text"]
        assert "print(" not in history[-1]["content"][0]["text"]
    assert [c["status"] for c in held["result"]["tool_calls"]] == ["OK", "PENDING_APPROVAL"]
    held_call = held["result"]["tool_calls"][1]
    assert held_call["tool"] == "close_ticket"
    assert held_call["input"] == {
        "customer_id": "customer-a",
        "ticket_id": "A-41",
        "reason": "operator says resolved",
        "actor": ACTOR,
        "run_id": held["result"]["run_id"],
    }
    held_events = [
        entry.get("kind")
        for entry in api.tables["events"].rows.values()
        if entry.get("run_id") == held["result"]["run_id"]
    ]
    assert {"model.call_end", "guardrail.checked", "store.read", "approval.requested"} <= set(
        held_events
    )
    assert ("model.completion_repair" in held_events) is late_native
    assert "store.write" not in held_events
    assert held["result"]["tool_calls"][0]["status"] == "OK"
    assert held["result"]["tool_calls"][0]["input"] == {
        "customer_id": "customer-a",
        "actor": ACTOR,
        "run_id": held["result"]["run_id"],
    }
    assert held["result"]["effects"][0]["status"] == "HELD_FOR_APPROVAL"
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
    cards = api.app.current_approvals(api.app.demo_state("customer-a"))
    assert len(cards) == 1 and cards[0]["status"] == "PENDING"
    assert cards[0]["ticket_id"] == "A-41" and cards[0]["requested_by"] == "operator"
    assert cards[0]["customer_id"] == ACTOR["customer_id"]
    assert cards[0]["reason"] == held_call["input"]["reason"]
    methods = [name for name, _, _ in server.received]
    assert methods.count("initialize") == 1 and methods.count("tools/list") == 1
    assert [row["params"]["name"] for name, row, _ in server.received if name == "tools/call"] == [
        "TicketTools___list_tickets"
    ]

    wire = MCPWire("real_tools", actor=ACTOR, token="offline")
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    monkeypatch.setattr(api.app.mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    approved_status, approved = request(
        api, "/api/run", {"mode": "approve", "approval": cards[0]["id"]}
    )
    assert approved_status == 200 and approved["result"]["status"] == "CONFIRMED"
    assert [m["method"] for m, _ in wire.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert [row["params"]["name"] for name, row, _ in server.received if name == "tools/call"] == [
        "TicketTools___list_tickets",
        "TicketTools___close_ticket",
    ]
    assert len(model.requests) == 3  # approval replay has no second model turn
    assert approved["result"]["effects"][0]["target"] == "A-41"
    assert approved["result"]["effects"][0]["confirmed"] is True
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "CLOSED"
    run_id = approved["result"]["run_id"]
    writes = [
        item
        for item in api.tables["events"].rows.values()
        if item.get("run_id") == run_id and item.get("kind") == "store.write"
    ]
    assert len(writes) == 1 and writes[0]["effect"]["ticket_id"] == "A-41"
    state = api.app.demo_state("customer-a")
    assert api.app.current_approvals(state)[0]["status"] == "APPROVED"
    assert state["policy_session"] != "policy-1" and not state.get("busy_owner")


@pytest.mark.parametrize("bad", ("B-77", True, {"ticket_id": "A-44"}, ["A-44"], "A-44" * 10))
def test_invalid_fourth_target_is_refused_before_runtime_mcp_or_model(
    api, local_mcp, monkeypatch, bad
):
    import strands.models.bedrock as bedrock_module

    from tests.test_security_regressions import request

    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-a",
            "ticket_id": "A-43",
            "status": "OPEN",
            "origin": "seed",
            "subject": "A-43",
            "body": "Own",
        }
    )
    model = Bedrock()
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    url, server, _, _ = local_mcp
    monkeypatch.setattr(
        api.app,
        "invoke_agent",
        lambda token, run_id, payload: runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            screen=Guardrail(),
            state_table=api.tables["state"],
        ),
    )
    status, _ = request(api, "/api/run", {"requests": ["A-41", "A-42", "A-43", bad]})
    assert status == 400
    assert server.received == [] and model.requests == []
    assert not api.app.demo_state("customer-a").get("busy_owner")
    assert not api.app.current_approvals(api.app.demo_state("customer-a"))
    assert api.tables["events"].rows == {}
    assert all(row["status"] == "OPEN" for row in api.tables["tickets"].rows.values())


@pytest.mark.parametrize("target", ("A-44", "A-45"))
def test_five_verified_api_targets_keep_late_card_provenance_and_one_approved_effect(
    api, local_mcp, monkeypatch, target
):
    import strands.models.bedrock as bedrock_module

    from tests.test_mcp_grant import MCPWire
    from tests.test_security_regressions import request
    from ticket_agent.events import Events as RuntimeEvents

    for ticket in ("A-43", "A-44", "A-45"):
        api.tables["tickets"].put_item(
            Item={
                "customer_id": "customer-a",
                "ticket_id": ticket,
                "status": "OPEN",
                "origin": "seed",
                "subject": ticket,
                "body": "Own test ticket",
            }
        )
    asked = ["A-41", "A-42", "A-43", "A-44", "A-45"]
    monkeypatch.setattr(RuntimeEvents, "_tbl", lambda self: api.tables["events"])
    model = Bedrock()
    model.request_close, model.close_target = True, target
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: model)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    server.expected_ticket = target
    server.admission_state = api.tables["state"]
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])
    payloads = []

    def invoke(token, run_id, payload):
        server.expected_run = run_id
        payloads.append(dict(payload))
        return runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            screen=Guardrail() if payload["mode"] == "live" else None,
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, out = request(api, "/api/run", {"requests": asked})
    held = out["result"]
    assert status == 200 and held["status"] == "AWAITING_APPROVAL"
    assert payloads[0]["requests"] == asked
    auth = next(
        e
        for e in api.tables["events"].rows.values()
        if e.get("run_id") == held["run_id"] and e.get("kind") == "auth.verified"
    )
    assert json.loads(auth["payload"])["request"]["operator asks the agent to close"] == asked
    assert all(t in model.requests[0]["messages"][0]["content"][0]["text"] for t in asked)
    assert len(model.requests) == 3 and not held["completion_repair_used"]
    assert [c["status"] for c in held["tool_calls"]] == ["OK", "PENDING_APPROVAL"]
    assert held["tool_calls"][1]["input"] == {
        "customer_id": "customer-a",
        "ticket_id": target,
        "reason": "operator says resolved",
        "actor": ACTOR,
        "run_id": held["run_id"],
    }
    assert "other pending cards are separate" not in held["note"]
    assert held["effects"][0]["status"] == "HELD_FOR_APPROVAL"
    cards = api.app.current_approvals(api.app.demo_state("customer-a"))
    assert len(cards) == 1 and cards[0]["requested_by"] == "operator"
    assert cards[0]["ticket_id"] == target and cards[0]["customer_id"] == "customer-a"
    assert cards[0]["reason"] == "operator says resolved" and cards[0]["run_id"] == held["run_id"]
    assert all(api.tables["tickets"].rows[("customer-a", t)]["status"] == "OPEN" for t in asked)
    assert [row["params"]["name"] for name, row, _ in server.received if name == "tools/call"] == [
        "TicketTools___list_tickets"
    ]

    wire = MCPWire("real_tools", actor=ACTOR, token="offline", target=target)
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    monkeypatch.setattr(api.app.mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    approved_status, approved = request(
        api, "/api/run", {"mode": "approve", "approval": cards[0]["id"]}
    )
    assert approved_status == 200 and approved["result"]["status"] == "CONFIRMED"
    assert [m["method"] for m, _ in wire.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert [row["params"]["name"] for name, row, _ in server.received if name == "tools/call"] == [
        "TicketTools___list_tickets",
        "TicketTools___close_ticket",
    ]
    assert len(model.requests) == 3  # approval never invokes a model
    assert approved["result"]["effects"][0]["target"] == target
    assert approved["result"]["effects"][0]["confirmed"] is True
    assert api.tables["tickets"].rows[("customer-a", target)]["status"] == "CLOSED"
    assert all(
        api.tables["tickets"].rows[("customer-a", t)]["status"] == "OPEN"
        for t in asked
        if t != target
    )
    writes = [
        e
        for e in api.tables["events"].rows.values()
        if e.get("run_id") == approved["result"]["run_id"] and e.get("kind") == "store.write"
    ]
    assert len(writes) == 1 and writes[0]["effect"]["ticket_id"] == target
    state = api.app.demo_state("customer-a")
    assert api.app.current_approvals(state)[0]["status"] == "APPROVED"
    assert state["policy_session"] != "policy-1" and not state.get("busy_owner")


@pytest.mark.parametrize("explicit_false", (False, True), ids=("captured_absent", "boolean_false"))
def test_actual_initialized_mcp_raw_schema_projected_through_real_bedrock(
    monkeypatch, local_mcp, explicit_false
):
    import strands.models.bedrock as bedrock_module

    fake = Bedrock()
    session = SimpleNamespace(region_name="us-east-1", client=lambda **kw: fake)
    monkeypatch.setattr(bedrock_module.boto3, "Session", lambda: session)
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    url, server, tickets, events = local_mcp
    if explicit_false:
        for row in server.raw_tools:
            row["inputSchema"]["additionalProperties"] = False
            row["inputSchema"]["properties"]["actor"]["additionalProperties"] = False
    result = runtime.run(
        {"run_id": "owned-run", "stage": "v1", "mode": "live", "operator": "alice"},
        "local",
        url,
        "us.meta.llama4-maverick-17b-instruct-v1:0",
        "owned-run",
        screen=Guardrail(),
        state_table=State("v1"),
    )
    methods = [name for name, _, _ in server.received]
    assert "initialize" in methods and methods.index("initialize") < methods.index("tools/list")
    assert methods.count("tools/list") == 1 and methods.count("tools/call") == 1
    assert result["tool_calls"][0]["status"] == "OK"
    assert result["tool_calls"][0]["input"] == {
        "customer_id": "customer-a",
        "actor": ACTOR,
        "run_id": "owned-run",
    }
    assert len(fake.requests) == 2 and "A-41 delivery" in result["answer"]
    assert tickets.rows[0]["status"] == "OPEN"
    reads = [e for e in events.items if e.get("kind") == "store.read"]
    assert len(reads) == 1 and reads[0]["tool"] == "list_tickets"
