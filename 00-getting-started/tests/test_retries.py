"""Real Bedrock serializer, parser and botocore retries; only HTTP send is a fixture.

Unlike Stubber (which bypasses transport retries), this supplies wire responses below
botocore's endpoint/retry handlers. No sockets, AWS calls or real credentials are used.
"""

import io
import json
import struct
import zlib

import httpx
import main
import pytest
from botocore.awsrequest import AWSResponse


class WireBody(io.BytesIO):
    def stream(self, amt=1024, decode_content=False):
        while chunk := self.read(amt):
            yield chunk


def event_frame(name, payload):
    """Encode an AWS event-stream frame, including both CRCs, for the real parser."""
    headers = bytearray()
    for key, value in {":message-type": "event", ":event-type": name, ":content-type": "application/json"}.items():
        key, value = key.encode(), value.encode()
        headers.extend(bytes([len(key)]) + key + b"\x07" + struct.pack("!H", len(value)) + value)
    body = json.dumps(payload).encode()
    prelude = struct.pack("!II", 16 + len(headers) + len(body), len(headers))
    message = prelude + struct.pack("!I", zlib.crc32(prelude)) + headers + body
    return message + struct.pack("!I", zlib.crc32(message))


def model_response(tool_loop, step):
    events = [("messageStart", {"role": "assistant"})]
    if tool_loop:
        events.extend(
            [
                (
                    "contentBlockStart",
                    {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"toolUseId": f"lookup-{step}", "name": "get_product_info"}},
                    },
                ),
                (
                    "contentBlockDelta",
                    {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"query":"PROD-002"}'}}},
                ),
            ]
        )
    else:
        events.append(("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "Smart Watch: $249.99"}}))
    events.extend(
        [
            ("contentBlockStop", {"contentBlockIndex": 0}),
            ("messageStop", {"stopReason": "tool_use" if tool_loop else "end_turn"}),
            (
                "metadata",
                {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 0}},
            ),
        ]
    )
    return b"".join(event_frame(name, payload) for name, payload in events)


@pytest.mark.parametrize(
    "mode, expected_status, attempts", [("recover", 200, 2), ("exhaust", 502, 2), ("loop", 200, 8)]
)
async def test_real_sdk_retry_path(monkeypatch, tmp_path, caplog, mode, expected_status, attempts):
    # Explicit fixture credential chain; never inspect a developer's HOME/profile.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent-credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "offline")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "offline")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    requests = []
    agents = []
    create_agent = main.create_agent

    def build():
        agent = create_agent()  # Actual pinned BedrockModel and Agent, including application Config/hooks.
        agents.append(agent)
        assert agent.model.client.meta.config.retries == {"mode": "standard", "total_max_attempts": 2}

        def send(request):
            requests.append(request)
            assert request.method == "POST"
            assert request.url.endswith("/converse-stream")
            body = json.loads(request.body)
            assert body["inferenceConfig"]["maxTokens"] == 512
            if mode == "exhaust" or len(requests) % 2:
                return AWSResponse(
                    request.url,
                    429,
                    {"content-type": "application/json", "x-amzn-errortype": "ThrottlingException"},
                    WireBody(b'{"message":"private-provider-detail"}'),
                )
            return AWSResponse(
                request.url,
                200,
                {"content-type": "application/vnd.amazon.eventstream"},
                WireBody(model_response(mode == "loop", len(requests) // 2)),
            )

        monkeypatch.setattr(agent.model.client._endpoint.http_session, "send", send)
        return agent

    monkeypatch.setattr(main, "create_agent", build)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await client.post("/invocations", json={"prompt": "private-fixture-prompt"})
        assert response.status_code == expected_status
        assert len(agents) == 1
        assert len(requests) == attempts
        for index in range(0, attempts, 2):
            assert requests[index].body == requests[index + 1].body
        if mode == "recover":
            assert response.json() == {"response": "Smart Watch: $249.99"}
        elif mode == "loop":
            assert attempts == main.MAX_MODEL_STEPS * main.MAX_TRANSPORT_ATTEMPTS
            assert response.json() == {"response": "Too many reasoning steps. Try a more specific question."}
        else:
            assert response.json()["error"] == "Model request failed. Check model access, region, and quotas."
        assert "private-provider-detail" not in response.text + caplog.text
        assert "private-fixture-prompt" not in response.text + caplog.text
    finally:
        for agent in agents:
            agent.model.client.close()
