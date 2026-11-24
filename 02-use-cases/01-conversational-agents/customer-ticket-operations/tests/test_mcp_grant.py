"""Initialized MCP wire contract and API approval, with no real network or credentials."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from strands.tools import mcp  # import before api fixture installs an isolated boto3 module

from functions.api import mcp_grant

pytest_plugins = ("tests.test_security_regressions",)

ACTOR = {"user_sub": "operator-sub", "customer_id": "customer-a"}
ARGS = {"customer_id": "customer-a", "ticket_id": "A-41", "actor": ACTOR}
TOOL = "TicketTools___confirm_close"
SCHEMA = {
    "type": "object",
    "required": ["customer_id", "ticket_id", "actor"],
    "properties": {
        "customer_id": {"type": "string"},
        "ticket_id": {"type": "string"},
        "actor": {
            "type": "object",
            "properties": {
                "user_sub": {"type": "string"},
                "customer_id": {"type": "string"},
            },
        },
    },
}


class Response:
    def __init__(self, status, body=b"", headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self.status

    def read(self, amount=-1):
        data, self.body = self.body[:amount] if amount >= 0 else self.body, b""
        return data

    def readline(self, amount=-1):
        line, separator, rest = self.body.partition(b"\n")
        data = line + separator
        if amount >= 0 and len(data) > amount:
            data, self.body = data[:amount], data[amount:] + rest
        else:
            self.body = rest
        return data


class OpenSSE(Response):
    def readline(self, amount=-1):
        if not self.body:
            raise AssertionError("server keeps SSE stream open after terminal result")
        return super().readline(amount)

    def read(self, amount=-1):
        raise AssertionError("bounded SSE must not wait for EOF")


class MCPWire:
    def __init__(self, scenario="ok", sse=False, actor=None, token="test-token", target="A-41"):
        self.scenario, self.sse, self.sent = scenario, sse, []
        self.actor, self.token, self.target = actor or ACTOR, token, target

    def open(self, request, timeout):
        assert 0 < timeout <= 30
        message = json.loads(request.data)
        headers = {k.lower(): v for k, v in request.header_items()}
        method = message["method"]
        self.sent.append((message, headers))
        assert headers["authorization"] == f"Bearer {self.token}"
        assert headers[mcp_grant.POLICY_HEADER] == "policy-1"
        assert headers["accept"] == "application/json, text/event-stream"
        if method == "initialize":
            if self.scenario == "unauthorized":
                return Response(401)
            assert "mcp-session-id" not in headers
            assert "mcp-protocol-version" not in headers
            assert message["params"]["protocolVersion"] == mcp_grant.PROTOCOL
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "offline gateway", "version": "1"},
            }
            if self.scenario == "missing_init_metadata":
                result.pop("serverInfo")
            if self.scenario == "unsupported_protocol":
                result["protocolVersion"] = "unsupported"
        else:
            if self.scenario == "stateless":
                assert "mcp-session-id" not in headers
            else:
                assert headers["mcp-session-id"] == "transport-1"
            assert headers["mcp-protocol-version"] == "2025-06-18"
            if method == "notifications/initialized":
                assert "id" not in message
                return Response(204 if self.scenario == "notification204" else 202)
            if method == "tools/list":
                result = {"tools": [{"name": TOOL, "inputSchema": SCHEMA}]}
            elif method == "tools/call":
                assert message["params"] == {
                    "name": TOOL,
                    "arguments": {**ARGS, "actor": self.actor, "ticket_id": self.target},
                }
                if self.scenario == "timeout":
                    raise TimeoutError("lost external response")
                result = {
                    "isError": False,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "status": "CONFIRMED",
                                    "customer_id": "customer-a",
                                    "ticket_id": self.target,
                                    "confirmed_by": self.actor,
                                }
                            ),
                        }
                    ],
                }
                if self.scenario == "real_tools":
                    from functions.ticket_tools import handler as tools

                    context = SimpleNamespace(
                        client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": TOOL})
                    )
                    confirmed = tools.lambda_handler(message["params"]["arguments"], context)
                    result["content"][0]["text"] = json.dumps(confirmed)
                if self.scenario == "foreign_target":
                    result["content"][0]["text"] = json.dumps(
                        {
                            "status": "CONFIRMED",
                            "customer_id": "customer-a",
                            "ticket_id": "A-42",
                            "confirmed_by": self.actor,
                        }
                    )
                if self.scenario == "missing_actor":
                    result["content"][0]["text"] = json.dumps(
                        {"status": "CONFIRMED", "customer_id": "customer-a", "ticket_id": "A-41"}
                    )
                if self.scenario == "policy_denied":
                    result = {
                        "isError": True,
                        "content": [{"type": "text", "text": "Policy denied"}],
                    }
                if self.scenario == "partial_result":
                    result = {"isError": False, "content": []}
                if self.scenario == "malformed_json_result":
                    result["content"][0]["text"] = "{"
            else:
                pytest.fail("unexpected MCP method")
        if self.scenario == "missing_schema" and method == "tools/list":
            result["tools"][0]["inputSchema"] = {"type": "object"}
        if self.scenario == "foreign_id" and method == "tools/call":
            response_id = "other-request"
        else:
            response_id = message["id"]
        envelope = json.dumps({"jsonrpc": "2.0", "id": response_id, "result": result})
        if self.scenario == "duplicate_response" and method == "tools/call":
            body = f"event: message\ndata: {envelope}\ndata: {envelope}\n\n".encode()
            mime = "text/event-stream"
        elif self.sse:
            body = f"event: message\ndata: {envelope}\n\n".encode()
            mime = "text/event-stream"
        else:
            body, mime = envelope.encode(), "application/json"
        if self.scenario == "oversized" and method == "tools/call":
            body = b"x" * (mcp_grant.MAX_REPLY + 1)
        session = (
            {"Mcp-Session-Id": "transport-1"}
            if method == "initialize" and self.scenario != "stateless"
            else {"Mcp-Session-Id": "transport-2"}
            if self.scenario == "session_switch" and method == "tools/list"
            else {}
        )
        cls = OpenSSE if self.scenario == "open_stream" else Response
        return cls(200, body, {"Content-Type": mime, **session})


@pytest.mark.parametrize(
    "sse,notification",
    [
        (False, "ok"),
        (True, "notification204"),
        (True, "open_stream"),
        (False, "stateless"),
    ],
)
def test_initialized_confirmation_has_exact_policy_and_transport_session(
    monkeypatch, sse, notification
):
    wire = MCPWire(notification, sse=sse)
    monkeypatch.setattr(mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    allowed, reason = mcp_grant.confirm_close(
        "https://gateway.invalid", "test-token", "policy-1", TOOL, ARGS
    )
    assert allowed and "confirmed exact" in reason
    assert [m["method"] for m, _ in wire.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert len({m["id"] for m, _ in wire.sent if "id" in m}) == 3
    assert all("run_id" not in m.get("params", {}).get("arguments", {}) for m, _ in wire.sent)


@pytest.mark.parametrize(
    "scenario,steps",
    [
        ("unauthorized", 1),
        ("missing_init_metadata", 1),
        ("unsupported_protocol", 1),
        ("missing_schema", 3),
        ("session_switch", 3),
        ("foreign_id", 4),
        ("duplicate_response", 4),
        ("oversized", 4),
        ("partial_result", 4),
        ("malformed_json_result", 4),
        ("foreign_target", 4),
        ("missing_actor", 4),
        ("policy_denied", 4),
    ],
)
def test_malformed_mcp_result_never_grants_or_retries(monkeypatch, scenario, steps):
    wire = MCPWire(scenario)
    monkeypatch.setattr(mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    allowed, reason = mcp_grant.confirm_close(
        "https://gateway.invalid", "test-token", "policy-1", TOOL, ARGS
    )
    assert not allowed and reason
    assert len(wire.sent) == steps
    assert sum(m["method"] == "tools/call" for m, _ in wire.sent) <= 1


def test_api_initialized_approval_to_actual_runtime_recorder_tool_and_store(api, monkeypatch):
    from functions.ticket_tools import handler as tools
    from tests.test_security_regressions import ALICE, held, request
    from ticket_agent import agent as runtime

    card = held(api, monkeypatch, "A-41")
    actor = {"user_sub": ALICE["sub"], "customer_id": "customer-a"}
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])
    wire = MCPWire("real_tools", actor=actor, token="offline")
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    monkeypatch.setattr(api.app.mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    monkeypatch.setattr(
        runtime, "claims_of", lambda token: {**actor, "sub": actor["user_sub"], "role": "operator"}
    )
    calls = []

    class Gateway:
        def __init__(self, url, headers):
            assert url == "https://gateway.invalid"
            assert headers["Authorization"] == "Bearer offline"
            assert headers["x-amzn-bedrock-agentcore-policy-session-id"] == "policy-1"

        def start(self):
            pass

        def stop(self, *args):
            pass

        def list_tools_sync(self):
            return [SimpleNamespace(tool_name="TicketTools___close_ticket")]

        def call_tool_sync(self, tool_use_id, name, args):
            assert name == "TicketTools___close_ticket"
            assert args["actor"] == actor and args["ticket_id"] == "A-41"
            assert api.app.demo_state("customer-a")["approval_dispatch"]
            calls.append((tool_use_id, name, dict(args)))
            context = SimpleNamespace(
                client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": name})
            )
            reply = tools.lambda_handler(args, context)
            return {"status": "success", "content": [{"text": json.dumps(reply)}]}

    monkeypatch.setattr(mcp, "MCPClient", Gateway)

    def invoke(token, run_id, payload):
        assert payload["approval"] == card["id"] and payload["policy_session"] == "policy-1"
        return runtime.run(
            payload,
            token,
            "https://gateway.invalid",
            "offline",
            run_id,
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, body = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    assert status == 200 and body["result"]["status"] == "CONFIRMED", body["result"].get(
        "tool_calls"
    )
    assert [m["method"] for m, _ in wire.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert len(calls) == 1 and calls[0][2]["run_id"] == body["result"]["run_id"]
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "CLOSED"
    assert body["result"]["effects"] == [
        {
            "operation": "close_ticket",
            "target": "A-41",
            "customer_id": "customer-a",
            "status": "EXECUTED",
            "before": "OPEN",
            "after": "CLOSED",
            "confirmed": True,
        }
    ]
    run_id = body["result"]["run_id"]
    writes = [
        item
        for item in api.tables["events"].rows.values()
        if item.get("run_id") == run_id and item.get("kind") == "store.write"
    ]
    assert len(writes) == 1 and writes[0]["effect"]["ticket_id"] == "A-41"
    state = api.app.demo_state("customer-a")
    assert not state.get("busy_owner") and not state.get("approval_dispatch")
    assert state["policy_session"] != "policy-1"
    assert api.app.current_approvals(state)[0]["status"] == "APPROVED"


@pytest.mark.parametrize("scenario", ["missing_schema", "foreign_id", "foreign_target", "timeout"])
def test_api_failed_mcp_grant_rotates_session_without_dispatch(api, monkeypatch, scenario):
    from tests.test_security_regressions import ALICE, held, request

    card = held(api, monkeypatch, "A-41")
    actor = {"user_sub": ALICE["sub"], "customer_id": "customer-a"}
    wire = MCPWire(scenario, actor=actor, token="offline")
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    monkeypatch.setattr(api.app.mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    monkeypatch.setattr(api.app, "invoke_agent", lambda *args: pytest.fail("grant not proven"))
    status, _ = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    assert (
        status == 502
        and api.app.current_approvals(api.app.demo_state("customer-a"))[0]["status"] == "FAILED"
    )
    state = api.app.demo_state("customer-a")
    assert state["policy_session"] != "policy-1" and not state.get("busy_owner")
    assert not state.get("approval_uncertain") and not state.get("approval_dispatch")
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
    assert sum(m["method"] == "tools/call" for m, _ in wire.sent) <= 1


def test_confirmation_requires_https_and_bound_policy_session_before_any_transport(monkeypatch):
    attempts = []
    monkeypatch.setattr(mcp_grant.urllib.request, "build_opener", lambda *a: attempts.append(a))
    for url, session in (
        ("http://gateway.invalid", "policy-1"),
        ("https://gateway.invalid", "bad session"),
        ("https://user@gateway.invalid", "policy-1"),
    ):
        assert mcp_grant.confirm_close(url, "test-token", session, TOOL, ARGS)[0] is False
    assert attempts == []


def test_lost_reply_propagates_uncertainty_without_retry(monkeypatch):
    wire = MCPWire("timeout")
    monkeypatch.setattr(mcp_grant.urllib.request, "build_opener", lambda *a: wire)
    with pytest.raises(TimeoutError, match="lost external response"):
        mcp_grant.confirm_close("https://gateway.invalid", "test-token", "policy-1", TOOL, ARGS)
    assert sum(m["method"] == "tools/call" for m, _ in wire.sent) == 1
