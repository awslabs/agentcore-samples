"""MCP v2 ASGI fixture: real SDK initialization, discovery, tool call and AWS signing, no AWS."""

import asyncio
import contextlib
import io
import unittest
from unittest.mock import patch

import httpx2
import raw_mcp_call
from botocore.credentials import Credentials
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

URL = "https://fixture.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


class Retrieve(unittest.TestCase):
    def test_signed_mcp_round_trip(self):
        server = MCPServer("fixture")
        queries, signed_requests = [], []

        @server.tool(name="knowledge___Retrieve")
        def retrieve(retrievalQuery: dict) -> dict:
            queries.append(retrievalQuery)
            return {
                "retrievalResults": [
                    {
                        "score": 0.9,
                        "content": {"type": "TEXT", "text": "fixture warranty"},
                        "location": {"type": "fixture"},
                    }
                ]
            }

        app = server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                allowed_hosts=["fixture.gateway.bedrock-agentcore.us-east-1.amazonaws.com"]
            ),
        )

        async def checked(scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                auth = headers.get(b"authorization", b"")
                self.assertIn(b"Credential=fixture/", auth)
                self.assertIn(b"/us-east-1/bedrock-agentcore/aws4_request", auth)
                signed_requests.append(scope["method"])
            await app(scope, receive, send)

        def factory(**kwargs):
            return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=checked), **kwargs)

        def transport(**kwargs):
            return aws_iam_streamablehttp_client(
                **kwargs, credentials=Credentials("fixture", "fixture"), httpx_client_factory=factory
            )

        async def go():
            async with app.router.lifespan_context(app):
                with (
                    patch.object(raw_mcp_call, "aws_iam_streamablehttp_client", transport),
                    contextlib.redirect_stdout(io.StringIO()) as out,
                ):
                    self.assertEqual(await raw_mcp_call.run("warranty", URL, "us-east-1", "knowledge"), 0)
                    self.assertIn("fixture warranty", out.getvalue())

        asyncio.run(go())
        self.assertEqual(queries, [{"text": "warranty"}])
        self.assertGreaterEqual(signed_requests.count("POST"), 3)

    def test_wrong_endpoint(self):
        with self.assertRaises(ValueError):
            asyncio.run(raw_mcp_call.run("test", "https://example.org/mcp", "us-east-1", "knowledge"))
