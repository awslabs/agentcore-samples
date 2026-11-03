"""Offline tests for the Web Answer sample."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from utils import gateway_auth, gateway_setup


def load_raw_mcp_module():
    path = SAMPLE_ROOT / "01-raw-mcp" / "raw_mcp_call.py"
    spec = importlib.util.spec_from_file_location("raw_mcp_call", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class GatewayAuthTests(unittest.TestCase):
    @patch.object(gateway_auth.requests, "post")
    def test_get_oauth_token_uses_cognito_client_credentials(self, post):
        response = Mock()
        response.json.return_value = {"access_token": "test-token"}
        post.return_value = response

        token = gateway_auth.get_oauth_token(
            domain="example",
            client_id="client-id",
            client_secret="client-secret",
            scope="agentcore-webanswer/invoke",
            region="us-east-1",
        )

        self.assertEqual(token, "test-token")
        post.assert_called_once_with(
            "https://example.auth.us-east-1.amazoncognito.com/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "scope": "agentcore-webanswer/invoke",
            },
            timeout=10,
        )
        response.raise_for_status.assert_called_once_with()

    def test_get_oauth_token_rejects_missing_credentials(self):
        with patch.multiple(
            gateway_auth,
            COGNITO_DOMAIN="",
            COGNITO_CLIENT_ID="",
            COGNITO_CLIENT_SECRET="",
        ), self.assertRaisesRegex(ValueError, "Cognito credentials"):
            gateway_auth.get_oauth_token()

    def test_transport_rejects_missing_gateway_url(self):
        with patch.object(gateway_auth, "GATEWAY_URL", ""), self.assertRaisesRegex(ValueError, "Gateway URL"):
            gateway_auth.create_streamable_http_transport()


class RawMCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_mcp = load_raw_mcp_module()

    def make_tool(self, properties, *, strands_wrapper=False):
        schema = {"type": "object", "properties": properties}
        input_schema = {"json": schema} if strands_wrapper else schema
        return SimpleNamespace(tool_spec={"inputSchema": input_schema})

    def test_uses_question_field_when_discovered(self):
        tool = self.make_tool({"question": {"type": "string"}})
        self.assertEqual(
            self.raw_mcp.input_arguments(tool, "What changed?"),
            {"question": "What changed?"},
        )

    def test_falls_back_to_query_field_in_strands_schema_wrapper(self):
        tool = self.make_tool(
            {"query": {"type": "string"}},
            strands_wrapper=True,
        )
        self.assertEqual(
            self.raw_mcp.input_arguments(tool, "What changed?"),
            {"query": "What changed?"},
        )

    def test_rejects_unknown_input_schema(self):
        tool = self.make_tool({"prompt": {"type": "string"}})
        with self.assertRaisesRegex(ValueError, "neither a 'question' nor a 'query'"):
            self.raw_mcp.input_arguments(tool, "What changed?")


class GatewaySetupTests(unittest.TestCase):
    @patch.object(gateway_setup, "wait_for_target", return_value=True)
    def test_creates_web_answer_connector_target(self, wait_for_target):
        client = Mock()
        client.create_gateway_target.return_value = {"targetId": "target-123"}

        target_id = gateway_setup.create_web_answer_target(client, "gateway-123")

        self.assertEqual(target_id, "target-123")
        client.create_gateway_target.assert_called_once_with(
            name="web-answer-tool",
            gatewayIdentifier="gateway-123",
            targetConfiguration={
                "mcp": {
                    "connector": {
                        "source": {"connectorId": "web-answer"},
                        "configurations": [
                            {"name": "WebAnswer", "parameterValues": {}}
                        ],
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        wait_for_target.assert_called_once_with(client, "gateway-123", "target-123")

    @patch.object(gateway_setup.time, "sleep")
    def test_gateway_role_has_web_answer_permission(self, _sleep):
        iam = Mock()
        iam.create_role.return_value = {
            "Role": {"Arn": "arn:aws:iam::123456789012:role/test-role"}
        }

        gateway_setup.create_gateway_role(
            iam,
            "test-role",
            "123456789012",
            "us-east-1",
        )

        policy = json.loads(iam.put_role_policy.call_args.kwargs["PolicyDocument"])
        web_answer = next(
            statement
            for statement in policy["Statement"]
            if statement["Sid"] == "InvokeWebAnswer"
        )
        self.assertEqual(
            web_answer["Action"],
            "bedrock-agentcore:InvokeWebAnswer",
        )
        self.assertEqual(
            web_answer["Resource"],
            "arn:aws:bedrock-agentcore:us-east-1:aws:tool/web-answer.v1",
        )


if __name__ == "__main__":
    unittest.main()
