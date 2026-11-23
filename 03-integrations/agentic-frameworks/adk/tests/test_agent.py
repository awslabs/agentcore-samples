"""Offline ADK/Runtime integration tests; replace only the provider response."""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from google.genai import Client, types
from google.genai._api_client import BaseApiClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adk_agent_google_search as app
from provider_credentials import BedrockAgentCoreContext


async def invoke(payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://test") as client:
        return await client.post("/invocations", json=payload)


class GoogleSearchTests(unittest.TestCase):
    def test_invalid_payload_does_not_request_key(self):
        with patch.object(app, "get_api_key", new_callable=AsyncMock) as get_key:
            for payload in ([], {"prompt": " "}, {"user_id": 4}, {"include_grounding": "yes"}, {"prompt": "x" * 4001}):
                self.assertEqual(asyncio.run(invoke(payload)).status_code, 400)
            get_key.assert_not_awaited()

    def test_deployed_request_never_falls_back_to_local_credentials(self):
        with (
            patch.dict(os.environ, LOCAL_DEV="0", GEMINI_API_KEY="fixture", GEMINI_PROVIDER_NAME="fixture"),
            patch.object(BedrockAgentCoreContext, "get_workload_access_token", return_value=None),
        ):
            self.assertEqual(asyncio.run(invoke({})).status_code, 503)

    def test_identity_vends_key_for_the_current_request_only(self):
        from bedrock_agentcore.identity import auth
        from provider_credentials import get_api_key

        identity = Mock(get_api_key=AsyncMock(return_value="fixture-provider-key"))
        with (
            patch.dict(os.environ, LOCAL_DEV="0", GEMINI_PROVIDER_NAME="fixture-provider"),
            patch.object(BedrockAgentCoreContext, "get_workload_access_token", return_value="fixture-token"),
            patch.object(auth, "IdentityClient", return_value=identity),
        ):
            self.assertEqual(asyncio.run(get_api_key()), "fixture-provider-key")
        identity.get_api_key.assert_awaited_once_with(
            provider_name="fixture-provider", agent_identity_token="fixture-token"
        )

    def test_real_runner_requests_search_and_returns_attribution(self):
        requests = []
        closed = []

        async def respond(_, method, path, body, options):
            requests.append(body)
            self.assertEqual(method, "post")
            self.assertIn(app.MODEL_ID, path)
            self.assertIn({"googleSearch": {}}, body["tools"])
            text = body["contents"][-1]["parts"][0]["text"]
            response = {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "answer: " + text}]},
                        "finishReason": "STOP",
                        "groundingMetadata": {
                            "webSearchQueries": ["fixture query"],
                            "groundingChunks": [{"web": {"uri": "https://example.invalid/source", "title": "Source"}}],
                        },
                    }
                ]
            }
            return types.HttpResponse(body=json.dumps(response), headers={})

        def make_client(**kwargs):
            client = Client(**kwargs)
            original = client.close

            def close():
                original()
                closed.append(True)

            client.close = close
            return client

        async def run():
            return await asyncio.gather(
                invoke({"prompt": "first"}), invoke({"prompt": "second", "include_grounding": True})
            )

        with (
            patch.dict(os.environ, LOCAL_DEV="1", GEMINI_API_KEY="fixture"),
            patch.object(BaseApiClient, "async_request", respond),
            patch.object(app, "Client", side_effect=make_client),
        ):
            text, grounded = asyncio.run(run())
        self.assertEqual(text.status_code, 200, text.text)
        self.assertEqual(grounded.status_code, 200, grounded.text)
        self.assertIn("answer: first", text.text)
        self.assertEqual(grounded.json()["answer"], "answer: second")
        self.assertEqual(
            grounded.json()["grounding"]["grounding_chunks"][0]["web"]["uri"], "https://example.invalid/source"
        )
        self.assertEqual(len(closed), 2)
        self.assertTrue(all(len(req["contents"]) == 1 for req in requests))

    def test_provider_failure_is_redacted_and_clients_closed(self):
        clients = []

        def make_client(**kwargs):
            client = Client(**kwargs)
            client.close = Mock(wraps=client.close)
            client.aio.aclose = AsyncMock(wraps=client.aio.aclose)
            clients.append(client)
            return client

        with (
            patch.dict(os.environ, LOCAL_DEV="1", GEMINI_API_KEY="fixture"),
            patch.object(BaseApiClient, "async_request", side_effect=RuntimeError("private-upstream-marker")),
            patch.object(app, "Client", side_effect=make_client),
        ):
            result = asyncio.run(invoke({"prompt": "hi"}))
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("private-upstream-marker", result.text)
        clients[0].close.assert_called_once_with()
        clients[0].aio.aclose.assert_awaited_once_with()
