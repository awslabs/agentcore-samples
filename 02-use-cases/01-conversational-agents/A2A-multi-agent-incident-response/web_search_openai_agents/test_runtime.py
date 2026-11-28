"""Offline contracts: no live search, model, or AWS credentials."""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.tool_context import ToolContext
from httpx import ASGITransport, AsyncClient


class SearchRuntimeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # The sample reads its deployment configuration at import time.
        config = patch.dict(
            os.environ,
            {
                "AWS_EC2_METADATA_DISABLED": "true",
                "MCP_REGION": "us-west-2",
                "MEMORY_ID": "fixture",
                "OPENAI_API_KEY": "fixture",
                "TAVILY_API_KEY": "fixture",
            },
        )
        config.start()
        self.addCleanup(config.stop)

    async def test_card_and_ping(self):
        from main import app

        async with AsyncClient(transport=ASGITransport(app), base_url="http://fixture") as http:
            self.assertEqual((await http.get("/ping")).json(), {"status": "healthy"})
            card = (await http.get("/.well-known/agent-card.json")).json()
            self.assertIn("websearch", [skill["id"] for skill in card["skills"]])

    async def test_request_local_memory_binding(self):
        from a2a.utils.errors import ServerError
        from agent_executor import WebSearchAgentExecutor

        executor = WebSearchAgentExecutor()
        with patch("tools.client", autospec=True) as memory:
            memory.get_memory_strategies.return_value = [
                {"type": "summary", "namespaces": ["/summaries/{actorId}/{sessionId}"]}
            ]
            memory.retrieve_memories.return_value = [{"content": {"text": "fixture history"}}]
            for actor in ("alice", "bob"):
                agent = await executor._get_agent(actor + "-session", actor)
                search = next(tool for tool in agent.tools if tool.name == "search_memory_by_namespace")
                args = json.dumps({"query": "fixture", "namespace_type": "summary", "top_k": 2})
                result = await search.on_invoke_tool(
                    ToolContext(context=None, tool_name=search.name, tool_call_id="fixture", tool_arguments=args), args
                )
                self.assertIn("fixture history", result)
                self.assertEqual(
                    memory.retrieve_memories.call_args.kwargs["namespace"], f"/summaries/{actor}/{actor}-session"
                )
        with self.assertRaises(ServerError):
            await executor.execute(SimpleNamespace(call_context=None), None)

    async def test_search_tool(self):
        from tools import web_search_impl

        with patch("tools.TavilyClient") as provider:
            provider.return_value.search.return_value = {"results": [{"title": "Fixture", "content": "sample"}]}
            result = await web_search_impl.on_invoke_tool(
                ToolContext(context=None, tool_name="web_search_impl", tool_call_id="fixture", tool_arguments="{}"),
                json.dumps({"query": "sandbox", "top_k": 12, "recency_days": 2}),
            )
            self.assertEqual(result["results"][0]["snippet"], "sample")
            kwargs = provider.return_value.search.call_args.kwargs
            self.assertEqual((kwargs["max_results"], kwargs["time_range"]), (10, "week"))
