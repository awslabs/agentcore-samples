import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import boto3
from botocore.stub import Stubber

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "agent"), str(ROOT / "mcp")]
import agent_obo
import mcp_server_obo as server


class TwoTokens(unittest.TestCase):
    def test_flow_separation(self):
        client = boto3.client(
            "bedrock-agentcore", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        app = "11111111-1111-1111-1111-111111111111"
        common = {"workloadIdentityToken": "runtime-fixture", "resourceCredentialProviderName": "owned"}
        with Stubber(client) as stub:
            stub.add_response(
                "get_resource_oauth2_token",
                {"accessToken": "transport"},
                {**common, "scopes": [f"api://{app}/.default"], "oauth2Flow": "M2M"},
            )
            stub.add_response(
                "get_resource_oauth2_token",
                {"accessToken": "graph"},
                {**common, "scopes": ["User.Read"], "oauth2Flow": "ON_BEHALF_OF_TOKEN_EXCHANGE"},
            )
            self.assertEqual(
                agent_obo.token_headers(client, "runtime-fixture", "owned", app),
                {"Authorization": "Bearer transport", agent_obo.HEADER: "graph"},
            )
            stub.assert_no_pending_responses()
        with self.assertRaises(ValueError):
            agent_obo.token_headers(client, None, "owned", app)

    def test_handler_closes_identity_on_failed_exchange(self):
        client = boto3.client(
            "bedrock-agentcore", region_name="us-west-2", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        with (
            patch.dict(
                "os.environ",
                AWS_REGION="us-west-2",
                MCP_RUNTIME_ARN="arn:aws:bedrock-agentcore:us-west-2:111111111111:runtime/demo-0123456789",
                CREDENTIAL_PROVIDER_NAME="fixture",
                ENTRA_MCP_CLIENT_ID="fixture",
            ),
            patch.object(agent_obo.boto3, "client", return_value=client),
            patch.object(agent_obo.BedrockAgentCoreContext, "get_workload_access_token", return_value="fixture"),
            patch.object(agent_obo, "token_headers", side_effect=ValueError("exchange failed")) as exchange,
            patch.object(client, "close", wraps=client.close) as close,
            patch.object(agent_obo, "MCPClient") as transport,
            self.assertRaisesRegex(ValueError, "exchange failed"),
        ):
            agent_obo.handler({"prompt": "fixture"})
        self.assertIs(exchange.call_args.args[0], client)
        close.assert_called_once_with()
        transport.assert_not_called()

    def test_endpoint_scope(self):
        arn = "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/profile-1234567890"
        self.assertIn("runtime%2Fprofile", agent_obo.destination(arn, "us-east-1"))
        for bad in ["https://untrusted.invalid/", arn.replace("us-east-1", "us-west-2")]:
            with self.assertRaises(ValueError):
                agent_obo.destination(bad, "us-east-1")

    def test_graph_uses_only_delegated_token(self):
        schemas = asyncio.run(server.mcp.list_tools())
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0].input_schema.get("properties"), {})
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                request=SimpleNamespace(headers={"authorization": "Bearer transport", server.HEADER: "graph"})
            )
        )
        http = AsyncMock()
        http.get.return_value = Mock(
            status_code=200, json=lambda: {"displayName": "Test", "jobTitle": "Example", "id": "not returned"}
        )
        with patch.object(server.httpx, "AsyncClient") as factory:
            factory.return_value.__aenter__.return_value = http
            self.assertEqual(asyncio.run(server.get_my_profile(ctx)), {"displayName": "Test", "jobTitle": "Example"})
            http.get.assert_awaited_once_with(
                "https://graph.microsoft.com/v1.0/me",
                params={"$select": "displayName,jobTitle"},
                headers={"Authorization": "Bearer graph"},
            )
            del ctx.request_context.request.headers[server.HEADER]
            with self.assertRaises(ValueError):
                asyncio.run(server.get_my_profile(ctx))
            self.assertEqual(http.get.await_count, 1)
