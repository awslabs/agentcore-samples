import asyncio
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import boto3
import httpx
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.response import StreamingBody
from example import agent as producer
from strands.models.model import Model
from streamlit.testing.v1 import AppTest
from transport import chunks, region_for

ARN = "arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/demo-1234567890"


class WeatherModel(Model):
    def get_config(self):
        return {"model_id": "offline"}

    def update_config(self, **kwargs):
        pass

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        if len(messages) == 1:
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "weather-1", "name": "weather"}},
                }
            }
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}}}
            reason = "tool_use"
        else:
            assert "Sunny (simulated)" in json.dumps(messages)
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Demo: "}}}
            yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "sunny ☀"}}}
            reason = "end_turn"
        yield {"contentBlockStop": {"contentBlockIndex": 0}}
        yield {"messageStop": {"stopReason": reason}}
        yield {
            "metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 0}}
        }


def response(raw, content_type):
    return {"response": StreamingBody(io.BytesIO(raw), len(raw)), "contentType": content_type}


class Contracts(unittest.TestCase):
    def test_streaming_producer_to_consumer(self):
        async def request():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=producer.app), base_url="http://test"
            ) as client:
                return await client.post("/invocations", json={"prompt": "demo weather?"})

        with (
            patch.dict(os.environ, {"BEDROCK_MODEL_ID": "fixture"}),
            patch.object(producer, "BedrockModel", return_value=WeatherModel()),
        ):
            result = asyncio.run(request())
        self.assertEqual(result.status_code, 200)
        for show in (True, False):
            client = Mock(
                invoke_agent_runtime=Mock(return_value=response(result.content, result.headers["content-type"]))
            )
            parts = list(chunks(client, ARN, "fixture-session", "demo weather?", show))
            self.assertEqual(parts[-2:], ["Demo: ", "sunny ☀"])
            self.assertEqual(any("Using tool: weather" in part for part in parts), show)

    def test_completed_json_contract(self):
        complete = BedrockAgentCoreApp()

        @complete.entrypoint
        def answer(payload):
            return {"response": "fixture answer"}

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=complete), base_url="http://test") as client:
                return await client.post("/invocations", json={"prompt": "question"})

        r = asyncio.run(request())
        client = Mock(invoke_agent_runtime=Mock(return_value=response(r.content, r.headers["content-type"])))
        self.assertEqual(list(chunks(client, ARN, "fixture", "question")), ["fixture answer"])

    def test_incremental_boundaries_and_failure(self):
        body = Mock()

        def blocks():
            yield b'data: "earl'
            yield b'y"\r\n\r\n'
            yield b'data: {"error":"private details"}\n\n'

        body.iter_chunks.return_value = blocks()
        client = Mock(invoke_agent_runtime=Mock(return_value={"contentType": "text/event-stream", "response": body}))
        stream = chunks(client, ARN, "fixture", "test")
        self.assertEqual(next(stream), "early")
        with self.assertRaisesRegex(ValueError, "Unsupported response object"):
            next(stream)
        body.close.assert_called_once()

    def test_streamlit_chat_state(self):
        self.assertEqual(region_for(ARN), "us-west-2")
        client = boto3.client(
            "bedrock-agentcore", region_name="us-west-2", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )

        with (
            patch.dict(os.environ, {"AGENT_RUNTIME_ARN": ARN}),
            patch("boto3.client", return_value=client),
            patch.object(
                client, "invoke_agent_runtime", return_value=response(b'{"response":"hello"}', "application/json")
            ),
            patch.object(client, "close", wraps=client.close) as close,
        ):
            ui = AppTest.from_file("app.py").run()
            ui.chat_input[0].set_value("Hello").run()
            self.assertFalse(ui.exception)
            self.assertEqual(ui.session_state["messages"], [("user", "Hello"), ("assistant", "hello")])
            close.assert_called_once_with()
            session = ui.session_state["runtime_session_id"]
            ui.button[0].click().run()
            self.assertNotEqual(ui.session_state["runtime_session_id"], session)
            self.assertEqual(ui.session_state["messages"], [])
