"""Both supervisor Gateway clients, through real Strands/MCP and controlled HTTP.

From the repo root with either supervisor's image dependencies installed:
python -m unittest discover -s 05-blueprints/tests -p test_concierge_gateway.py
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://sample.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def samples():
    for name in ("shopping", "travel"):
        path = ROOT / f"{name}-concierge-agent/concierge_agent/supervisor_agent/gateway_client.py"
        spec = importlib.util.spec_from_file_location(f"{name}_gateway", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


class ConciergeGatewayTests(unittest.TestCase):
    def test_invalid_service_url_refused_before_token_acquisition(self):
        for module in samples():
            for url in (
                "http://example.invalid/mcp",
                "https://foreign.example/mcp",
                URL + "?access_token=x",
                URL.replace("us-east-1", "us-west-2"),
            ):
                with (
                    self.subTest(module=module.__name__, url=url),
                    patch.dict(os.environ, AWS_REGION="us-east-1"),
                    patch.object(module, "get_ssm_parameter", return_value=url),
                    patch.object(module, "get_gateway_access_token") as token,
                    self.assertRaises(ValueError),
                ):
                    try:
                        module.get_gateway_client("^carttools___")
                    finally:
                        token.assert_not_called()

    def test_authenticated_handshake_and_filtered_tools(self):
        for module in samples():
            methods = []

            async def send(_, request, methods=methods):
                self.assertEqual(str(request.url), URL)
                self.assertEqual(request.headers["authorization"], "Bearer fixture")
                rpc = json.loads(await request.aread())
                methods.append(rpc["method"])
                if rpc["method"] == "notifications/initialized":
                    return httpx.Response(202, content=b"")
                if rpc["method"] == "initialize":
                    result = {
                        "protocolVersion": rpc["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                else:
                    self.assertEqual(rpc["method"], "tools/list")
                    result = {
                        "tools": [
                            {
                                "name": n,
                                "description": "Fixture only",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                            for n in ["carttools___lookup", "unrelated___lookup"]
                        ]
                    }
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})

            with (
                self.subTest(module=module.__name__),
                patch.dict(os.environ, AWS_REGION="us-east-1"),
                patch.object(module, "get_ssm_parameter", return_value=URL),
                patch.object(module, "get_gateway_access_token", return_value="fixture"),
                patch.object(httpx.AsyncHTTPTransport, "handle_async_request", send),
            ):
                with module.get_gateway_client("^carttools___") as client:
                    self.assertEqual([t.tool_name for t in client.list_tools_sync()], ["gateway_carttools___lookup"])
                self.assertIn("initialize", methods)
                self.assertIn("tools/list", methods)
