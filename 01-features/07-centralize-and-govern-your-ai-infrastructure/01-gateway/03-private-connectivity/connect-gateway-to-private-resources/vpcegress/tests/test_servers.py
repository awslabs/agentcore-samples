"""Run the real existing FastMCP applications in-process, with sockets denied.

Requires ../docker/fastmcp-mock/requirements.txt. This is native Python startup
and protocol/tool coverage, NOT a Linux container or ARM64 execution claim.
"""

import importlib.util
import runpy
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx
from fastmcp import Client, FastMCP

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), ROOT / "docker" / name / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ServerContracts(unittest.IsolatedAsyncioTestCase):
    async def test_real_order_server_tools(self):
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")
        ):
            server = load("fastmcp-mock")
            async with Client(server.mcp) as client:
                tools = await client.list_tools()
                self.assertEqual(
                    {tool.name for tool in tools}, {"echo", "add", "get_time"}
                )
                result = await client.call_tool("echo", {"message": "offline"})
                self.assertEqual(result.data, "offline")
                self.assertEqual(
                    (await client.call_tool("add", {"a": 2, "b": 3})).data, 5
                )

    async def test_real_stock_server_tools(self):
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")
        ):
            server = load("stock-mcp-mock")
            async with Client(server.mcp) as client:
                self.assertEqual(
                    {tool.name for tool in await client.list_tools()},
                    {"get_stock_price", "get_market_summary"},
                )
                self.assertEqual(
                    (
                        await client.call_tool("get_stock_price", {"symbol": "aapl"})
                    ).data["symbol"],
                    "AAPL",
                )
                self.assertIn(
                    "error",
                    (
                        await client.call_tool(
                            "get_stock_price", {"symbol": "not-a-symbol"}
                        )
                    ).data,
                )

    async def test_native_http_lifespan_and_initialize(self):
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")
        ):
            for name, endpoint in [
                ("fastmcp-mock", "/mcp"),
                ("stock-mcp-mock", "/stock-mcp/"),
            ]:
                server = load(name)
                app = server.mcp.http_app(path=endpoint)
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://offline",
                        follow_redirects=True,
                    ) as client,
                ):
                    response = await client.post(
                        endpoint,
                        headers={"Accept": "application/json, text/event-stream"},
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2025-03-26",
                                "capabilities": {},
                                "clientInfo": {
                                    "name": "offline-test",
                                    "version": "1.0",
                                },
                            },
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn("protocolVersion", response.text)
                    self.assertIn("serverInfo", response.text)

    async def test_container_entrypoints_ports_and_path(self):
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("NETWORK DENIED")
        ):
            for name, port in [("fastmcp-mock", 8000), ("stock-mcp-mock", 8001)]:
                with patch.object(FastMCP, "run") as run:
                    runpy.run_path(
                        str(ROOT / "docker" / name / "server.py"), run_name="__main__"
                    )
                    self.assertEqual(run.call_args.kwargs["port"], port)
                    self.assertEqual(
                        run.call_args.kwargs["transport"], "streamable-http"
                    )
                    if port == 8001:
                        self.assertEqual(run.call_args.kwargs["path"], "/stock-mcp/")
                dockerfile = (ROOT / "docker" / name / "Dockerfile").read_text()
                self.assertIn("USER 10001", dockerfile)
                self.assertIn('CMD ["python", "server.py"]', dockerfile)
                self.assertIn("--require-hashes", dockerfile)


if __name__ == "__main__":
    unittest.main()
