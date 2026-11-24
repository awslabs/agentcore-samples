"""Real Runtime entrypoint, controls and Gateway selection for builds V1 and V2."""

from types import SimpleNamespace

from tests.test_agent_controls import CAPTURE, TOKEN, FakeGateway, Guardrail, StateTable, screen

pytest_plugins = ["tests.test_agent_controls"]


def test_v1_screen_without_region_gateway_fence(app, monkeypatch):
    import sys

    table = StateTable({"busy_stage": "v1"})
    monkeypatch.setattr(
        sys.modules["boto3"], "resource", lambda name: SimpleNamespace(Table=lambda t: table)
    )
    guard = Guardrail("NONE")
    monkeypatch.setattr(app, "Screen", lambda identifier, version: screen(guard))
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    result = app.invoke(
        {"stage": "v1", "mode": "replay", "calls": CAPTURE, "run_id": "r1"}, context
    )
    assert FakeGateway.instances[0].url == "https://v0"
    assert guard.seen and result["stage"] == "v1"
    assert result["tool_calls"][1]["status"] == "OK"
    assert not any(c["control"] == "AgentCore Policy" for c in result["controls"])


def test_v2_region_gateway_fence_keeps_guardrail(app, monkeypatch):
    import sys

    table = StateTable({"busy_stage": "v2"})
    monkeypatch.setattr(
        sys.modules["boto3"], "resource", lambda name: SimpleNamespace(Table=lambda t: table)
    )
    guard = Guardrail("NONE")
    monkeypatch.setattr(app, "Screen", lambda identifier, version: screen(guard))
    FakeGateway.replies = {
        "close_ticket": {
            "status": "error",
            "content": [{"text": "Tool call denied by AgentCore Policy: not authorized"}],
        }
    }
    context = SimpleNamespace(request_headers={"Authorization": f"Bearer {TOKEN}"}, session_id="s")
    result = app.invoke(
        {"stage": "v2", "mode": "replay", "calls": CAPTURE, "run_id": "r1"}, context
    )
    assert FakeGateway.instances[0].url == "https://v1"
    assert guard.seen and result["stage"] == "v2"
    assert result["tool_calls"][1]["decision"] == "DENY"
    assert any(c["control"] == "AgentCore Policy" for c in result["controls"])
