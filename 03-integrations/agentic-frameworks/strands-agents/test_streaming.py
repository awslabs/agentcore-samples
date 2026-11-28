"""Drive the real Strands tool loop with OpenAI-compatible SSE fixture responses."""

import asyncio
import json
import os
import threading
import unittest
from unittest.mock import AsyncMock, patch

import httpx2
import strands_openai_identity as app
from openai import AsyncOpenAI


def frame(delta, finish=None):
    chunk = {
        "id": "fixture",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-4o",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return ("data: " + json.dumps(chunk) + "\n\n").encode()


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_tool_then_incremental_text(self):
        release = threading.Event()
        requests = []
        clients = []

        class TextStream(httpx2.AsyncByteStream):
            async def __aiter__(self):
                yield frame({"role": "assistant", "content": "Demo: "})
                if not await asyncio.to_thread(release.wait, 5):
                    raise TimeoutError("Consumer did not receive first chunk")
                yield frame({"content": "sunny."}) + frame({}, "stop") + b"data: [DONE]\n\n"

        def respond(request):
            self.assertEqual(request.url.host, "api.openai.com")
            self.assertEqual(request.headers["authorization"], "Bearer fixture-key")
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                self.assertEqual([t["function"]["name"] for t in body["tools"]], ["weather"])
                tool = {
                    "index": 0,
                    "id": "call_demo",
                    "type": "function",
                    "function": {"name": "weather", "arguments": '{"city":"Seattle"}'},
                }
                return httpx2.Response(
                    200,
                    headers={"Content-Type": "text/event-stream"},
                    content=frame({"role": "assistant", "tool_calls": [tool]}, "tool_calls") + b"data: [DONE]\n\n",
                )
            self.assertIn("Demo forecast for Seattle: sunny.", json.dumps(body["messages"]))
            return httpx2.Response(200, headers={"Content-Type": "text/event-stream"}, stream=TextStream())

        def create_client(**kwargs):
            transport = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
            clients.append(transport)
            return AsyncOpenAI(**kwargs, http_client=transport)

        with (
            patch.dict(os.environ, LOCAL_DEV="1", OPENAI_API_KEY="fixture-key"),
            patch.object(app, "AsyncOpenAI", side_effect=create_client),
        ):
            stream = await app.strands_agent_open_ai({"prompt": "Tell me the Seattle fixture forecast"})
            try:
                self.assertEqual(await asyncio.wait_for(anext(stream), 3), "Demo: ")
                release.set()
                self.assertEqual([x async for x in stream], ["sunny."])
            finally:
                release.set()
                await stream.aclose()
        self.assertEqual(len(requests), 2)
        self.assertTrue(clients[0].is_closed)

    async def test_http_sse_and_redacted_error(self):
        async def failed(*_args, **_kwargs):
            yield {"data": "first"}
            raise RuntimeError("private-marker")

        with (
            patch.object(app, "get_key", new_callable=AsyncMock, return_value="fixture"),
            patch.object(app.Agent, "stream_async", failed),
        ):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app.app), base_url="http://test"
            ) as client:
                r = await client.post("/invocations", json={"prompt": "hello"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
        self.assertEqual(events, ["first", {"error": "The response could not be completed."}])
        self.assertNotIn("private-marker", r.text)

    async def test_invalid_input_and_missing_identity_fail_before_model(self):
        with (
            patch.dict(os.environ, LOCAL_DEV="0", OPENAI_API_KEY="ignored", OPENAI_PROVIDER_NAME="fixture"),
            patch.object(app.BedrockAgentCoreContext, "get_workload_access_token", return_value=None),
            patch.object(app, "AsyncOpenAI", side_effect=AssertionError("model client should not be created")),
        ):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app.app), base_url="http://test"
            ) as client:
                self.assertEqual((await client.post("/invocations", json={"prompt": " "})).status_code, 400)
                self.assertEqual((await client.post("/invocations", json={"prompt": "hello"})).status_code, 503)
