"""Real Runtime and Agents SDK; both HTTP transports stay in process."""

import asyncio
import json

import httpx2
import openai_agents_handoff_example as sample
import pytest
import runtime_app
from openai import AsyncOpenAI


@pytest.mark.parametrize("specialist", ["travel_expert", "food_expert"])
def test_runtime_routes_to_specialist(monkeypatch, caplog, specialist):
    requests = []
    marker = "fixture-only-not-a-credential"
    monkeypatch.setenv("LOCAL_DEV", "1")
    monkeypatch.setenv("OPENAI_API_KEY", marker)

    def respond(request):
        assert request.url.host == "api.openai.com"
        assert request.headers["authorization"] == f"Bearer {marker}"
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            tool = f"transfer_to_{specialist}"
            assert tool in [t.get("name") for t in body["tools"]]
            item = {"type": "function_call", "name": tool, "arguments": "{}", "call_id": "call_example", "id": "fc_1"}
        else:
            assert any(t["type"].startswith("web_search") for t in body["tools"])
            assert ("destinations" if specialist == "travel_expert" else "restaurants") in body["instructions"]
            item = {
                "type": "message",
                "role": "assistant",
                "id": "msg_1",
                "status": "completed",
                "content": [{"type": "output_text", "text": "Specialist answer", "annotations": []}],
            }
        return httpx2.Response(
            200,
            json={
                "id": "resp_example",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "output": [item],
                "status": "completed",
            },
        )

    monkeypatch.setattr(
        runtime_app,
        "AsyncOpenAI",
        lambda **kw: AsyncOpenAI(**kw, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))),
    )
    response = asyncio.run(invoke({"prompt": "private fixture question"}))
    assert response.status_code == 200 and response.json() == {"result": "Specialist answer"}
    assert len(requests) == 2
    assert marker not in caplog.text and "private fixture question" not in caplog.text


async def invoke(payload):
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=sample.app), base_url="http://test") as client:
        return await client.post("/invocations", json=payload)


def test_no_cloud_fallback_to_local_secret(monkeypatch):
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    monkeypatch.setenv("OPENAI_PROVIDER_NAME", "example-provider")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-credential")
    assert asyncio.run(invoke({"prompt": "hello"})).status_code == 503
    assert asyncio.run(invoke({"prompt": []})).status_code == 400


def test_identity_uses_request_token_and_redacts_failure(monkeypatch, caplog):
    monkeypatch.delenv("LOCAL_DEV", raising=False)
    monkeypatch.setenv("OPENAI_PROVIDER_NAME", "example-provider")
    from bedrock_agentcore.identity import auth

    class Identity:
        def __init__(self, *_):
            pass

        async def get_api_key(self, *, provider_name, agent_identity_token):
            assert provider_name == "example-provider" and agent_identity_token == "fixture-token"
            raise RuntimeError("private-upstream-detail")

    monkeypatch.setattr(auth, "IdentityClient", Identity)
    monkeypatch.setattr(runtime_app.BedrockAgentCoreContext, "get_workload_access_token", lambda: "fixture-token")
    response = asyncio.run(invoke({"prompt": "hello"}))
    assert response.status_code == 502
    assert "private-upstream-detail" not in caplog.text + response.text
