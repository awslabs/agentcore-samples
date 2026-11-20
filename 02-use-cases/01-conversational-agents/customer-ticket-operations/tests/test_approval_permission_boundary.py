"""A valid native MCP grant; event-owner IAM must permit the post-grant update.

Only loopback stdlib HTTP and isolated conditional tables. Covers an Events table that
denies UpdateItem after the grant; it does not claim deployed IAM state.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from functions.ticket_tools import handler as tools
from tests.test_security_regressions import held, request
from tests.test_tool_projection_http import ACTOR
from ticket_agent import agent as runtime

pytest_plugins = ("tests.test_security_regressions", "tests.test_tool_projection_http")


class LoopbackOpener:
    """Connect urllib's genuine stdlib POST to a local MCP server after URL validation."""

    def __init__(self, url, build_opener, no_redirect):
        self.url = url
        self.opener = build_opener(no_redirect())

    def open(self, req, timeout):
        assert req.full_url == "https://gateway.invalid"
        local = urllib.request.Request(
            self.url, data=req.data, headers=dict(req.header_items()), method="POST"
        )
        return self.opener.open(local, timeout=timeout)


def test_canonical_native_mcp_and_scoped_events_permission_approves_once(
    api, local_mcp, monkeypatch
):
    from pathlib import Path

    from strands.tools import mcp
    from strands.tools.mcp.mcp_client import MCPClient as RealMCPClient

    # Terraform's actual separately scoped API permission, not broad Tickets/State access.
    policy = (Path(__file__).resolve().parents[1] / "infra/iac/roots/app/main.tf").read_text()
    events_statement = policy.split('sid       = "Events"', 1)[1].split("  statement {", 1)[0]
    assert '"dynamodb:UpdateItem"' in events_statement
    assert "resources = [aws_dynamodb_table.events.arn]" in events_statement

    card = held(api, monkeypatch, "A-41")
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_ticket = "A-41"
    server.expected_policy_session = "policy-1"
    server.admission_state = api.tables["state"]
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])
    monkeypatch.setattr(
        runtime,
        "claims_of",
        lambda token: {"sub": ACTOR["user_sub"], "customer_id": "customer-a", "role": "operator"},
    )
    monkeypatch.setattr(mcp, "MCPClient", RealMCPClient)
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    build_opener = urllib.request.build_opener
    monkeypatch.setattr(
        api.app.mcp_grant.urllib.request,
        "build_opener",
        lambda *args: LoopbackOpener(url, build_opener, api.app.mcp_grant.NoRedirect),
    )
    events_update = api.tables["events"].update_item
    owner_updates = []

    def exact_events_update(**kwargs):
        assert kwargs["UpdateExpression"] == "SET confirmed = :confirmed"
        assert kwargs["ConditionExpression"] == "attribute_exists(run_id)"
        assert kwargs["Key"]["sk"] == api.app.OWNER_SK
        assert kwargs["ExpressionAttributeValues"][":confirmed"] == ["A-41"]
        assert (
            api.tables["events"].rows[(kwargs["Key"]["run_id"], api.app.OWNER_SK)]["customer_id"]
            == "customer-a"
        )
        owner_updates.append(kwargs["Key"]["run_id"])
        return events_update(**kwargs)

    monkeypatch.setattr(api.tables["events"], "update_item", exact_events_update)
    calls = []

    def invoke(token, run_id, payload):
        assert payload["mode"] == "replay" and payload["approval"] == card["id"]
        assert payload["policy_session"] == "policy-1"
        server.expected_run = run_id
        calls.append(run_id)
        return runtime.run(
            payload,
            token,
            url,
            "us.meta.llama4-maverick-17b-instruct-v1:0",
            run_id,
            state_table=api.tables["state"],
        )

    monkeypatch.setattr(api.app, "invoke_agent", invoke)
    status, body = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    result = body["result"]
    assert status == 200 and result["status"] == "CONFIRMED"
    methods = [method for method, _, _ in server.received]
    assert methods.count("initialize") == methods.count("tools/list") == 2
    assert methods.count("notifications/initialized") == 2
    assert [
        row["params"]["name"] for method, row, _ in server.received if method == "tools/call"
    ] == ["TicketTools___confirm_close", "TicketTools___close_ticket"]
    assert owner_updates == calls == [result["run_id"]]
    assert result["tool_calls"][0]["input"]["actor"] == ACTOR
    assert result["tool_calls"][0]["input"]["run_id"] == result["run_id"]
    assert result["effects"] == [
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
    state = api.app.demo_state("customer-a")
    assert api.app.current_approvals(state)[0]["status"] == "APPROVED"
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "CLOSED"
    assert not state.get("busy_owner") and not state.get("approval_dispatch")
    assert state["policy_session"] != "policy-1"
    writes = [
        e
        for e in api.tables["events"].rows.values()
        if e.get("run_id") == result["run_id"] and e.get("kind") == "store.write"
    ]
    assert len(writes) == 1 and writes[0]["effect"]["ticket_id"] == "A-41"
    before = len(server.received)
    duplicate, _ = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    assert duplicate == 409 and len(server.received) == before


@pytest.mark.parametrize("reply", ("foreign_ticket", "foreign_actor", "not_confirmed", "malformed"))
def test_native_http_result_mismatch_retires_session_without_close_or_retry(
    api, local_mcp, monkeypatch, reply
):
    card = held(api, monkeypatch, "A-41")
    url, server, _, _ = local_mcp
    server.auth_token = "offline"
    server.expected_policy_session = "policy-1"
    server.expected_ticket = "A-41"
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])

    def mismatch(native):
        if reply == "malformed":
            return "not JSON"
        native = dict(native)
        if reply == "foreign_ticket":
            native["ticket_id"] = "B-77"
        elif reply == "foreign_actor":
            native["confirmed_by"] = {"customer_id": "customer-b", "user_sub": "other"}
        else:
            native["status"] = "NOT_CONFIRMED"
        return native

    server.confirm_override = mismatch
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    build_opener = urllib.request.build_opener
    monkeypatch.setattr(
        api.app.mcp_grant.urllib.request,
        "build_opener",
        lambda *args: LoopbackOpener(url, build_opener, api.app.mcp_grant.NoRedirect),
    )
    invoked = []
    monkeypatch.setattr(
        api.app,
        "invoke_agent",
        lambda *args: (
            invoked.append(args) or pytest.fail("unverified Gateway record dispatched to Runtime")
        ),
    )
    status, _ = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    methods = [method for method, _, _ in server.received]
    assert status == 502 and methods.count("tools/call") == 1
    assert not invoked
    state = api.app.demo_state("customer-a")
    assert state["policy_session"] != "policy-1"
    assert not state.get("busy_owner") and not state.get("approval_dispatch")
    assert api.app.current_approvals(state)[0]["status"] == "FAILED"
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
    again, _ = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    assert again == 409 and len(server.received) == len(methods)


def test_valid_native_gateway_grant_then_events_update_denial_causes_prior_502(
    api, local_mcp, monkeypatch
):
    card = held(api, monkeypatch, "A-41")
    url, server, _, _ = local_mcp
    server.auth_token, server.expected_policy_session, server.expected_ticket = (
        "offline",
        "policy-1",
        "A-41",
    )
    monkeypatch.setattr(tools, "_table", api.tables["tickets"])
    monkeypatch.setattr(tools, "_events", api.tables["events"])
    monkeypatch.setattr(
        api.app, "agent_param", lambda key, default: json.dumps({"v3": "https://gateway.invalid"})
    )
    build_opener = urllib.request.build_opener
    monkeypatch.setattr(
        api.app.mcp_grant.urllib.request,
        "build_opener",
        lambda *args: LoopbackOpener(url, build_opener, api.app.mcp_grant.NoRedirect),
    )
    # Assert the API's actual post-confirmation conditional state transitions, not a
    # coarse fake that ignores the owner, stage, session or recorded grant marker.
    updates = []
    state_update = api.tables["state"].update_item

    def strict_state_update(**kwargs):
        expression = kwargs["UpdateExpression"]
        condition = kwargs.get("ConditionExpression", "")
        values = kwargs["ExpressionAttributeValues"]
        if "approval_uncertain = :marker" in expression and "REMOVE" not in expression:
            assert condition == (
                "busy_owner = :owner AND policy_session = :session "
                "AND attribute_not_exists(approval_uncertain) AND attribute_not_exists(approval_dispatch)"
            )
            assert values[":owner"] == api.app.demo_state("customer-a")["busy_owner"]
            updates.append("mark")
        elif "approval_dispatch = :marker" in expression:
            assert condition == (
                "busy_owner = :owner AND policy_session = :session AND approval_uncertain = :marker"
            )
            state = api.app.demo_state("customer-a")
            assert state["busy_owner"] == values[":owner"]
            assert state["policy_session"] == values[":session"] == "policy-1"
            assert state["approval_uncertain"] == values[":marker"]
            updates.append("dispatch")
        elif "policy_session = :fresh" in expression:
            assert condition == "busy_owner = :owner AND policy_session = :session"
            updates.append("retire")
        return state_update(**kwargs)

    monkeypatch.setattr(api.tables["state"], "update_item", strict_state_update)
    # Simulate an Events role without UpdateItem (the shipped role grants it).
    monkeypatch.setattr(
        api.tables["events"],
        "update_item",
        lambda **kwargs: (_ for _ in ()).throw(PermissionError("Events UpdateItem is not granted")),
    )
    dispatched = []
    monkeypatch.setattr(
        api.app,
        "invoke_agent",
        lambda *args: (
            dispatched.append(args)
            or pytest.fail("must not replay close after failed owner-event transition")
        ),
    )
    status, body = request(api, "/api/run", {"mode": "approve", "approval": card["id"]})
    methods = [method for method, _, _ in server.received]
    assert status == 502 and "approval" in body["error"]
    assert methods.count("initialize") == methods.count("tools/list") == 1
    assert methods.count("notifications/initialized") == methods.count("tools/call") == 1
    assert [
        row["params"]["name"] for method, row, _ in server.received if method == "tools/call"
    ] == ["TicketTools___confirm_close"]
    assert not dispatched and updates == ["mark", "dispatch", "retire"]
    state = api.app.demo_state("customer-a")
    assert state["policy_session"] != "policy-1"
    assert not state.get("busy_owner") and not state.get("approval_dispatch")
    assert not state.get("approval_uncertain")
    assert api.app.current_approvals(state)[0]["status"] == "FAILED"
    assert api.tables["tickets"].rows[("customer-a", "A-41")]["status"] == "OPEN"
