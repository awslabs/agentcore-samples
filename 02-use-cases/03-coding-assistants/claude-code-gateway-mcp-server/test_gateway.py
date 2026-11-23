"""Offline setup, token handling and deletion receipts using modeled service responses."""

import io
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, closing, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
import gateway
from botocore.stub import ANY, Stubber

REGION = "us-east-1"
ACCOUNT = "111122223333"
NAME = "claude-knowledge-123456789abc"
GID = NAME + "-abcdefgh12"
ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{GID}"
URL = f"https://{GID}.gateway.bedrock-agentcore.{REGION}.amazonaws.com/mcp"
ROLE = f"arn:aws:iam::{ACCOUNT}:role/demo-gateway"
TARGET = "abcdefgh12"
POOL = REGION + "_fixture"
SCOPE = "demo/invoke"
AUTH = {
    "customJWTAuthorizer": {
        "discoveryUrl": f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}/.well-known/openid-configuration",
        "allowedClients": ["fixtureclient"],
        "allowedScopes": [SCOPE],
    }
}
TARGET_CONFIG = {"mcp": {"mcpServer": {"endpoint": gateway.KB_URL}}}
NOW = datetime(2026, 1, 1, tzinfo=UTC)
G = {
    "gatewayId": GID,
    "gatewayArn": ARN,
    "name": NAME,
    "createdAt": NOW,
    "updatedAt": NOW,
    "status": "READY",
    "authorizerType": "CUSTOM_JWT",
    "gatewayUrl": URL,
}
T = {
    "gatewayArn": ARN,
    "targetId": TARGET,
    "createdAt": NOW,
    "updatedAt": NOW,
    "status": "READY",
    "name": "aws-knowledge",
    "targetConfiguration": TARGET_CONFIG,
    # Botocore requires a nonempty list on its response shape. This fixture
    # exercises lifecycle handling only; the app ignores the returned auth field.
    # The expected create request below omits outbound credentials for the public target.
    "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(gateway, "STATE", root / "receipt.json"))
        self.stack.enter_context(patch.object(gateway, "MCP_CONFIG", root / ".mcp.json"))
        self.stack.enter_context(patch.object(gateway.secrets, "token_hex", return_value="123456789abc"))
        session = boto3.Session(region_name=REGION, aws_access_key_id="fixture", aws_secret_access_key="fixture")
        client = self.stack.enter_context(closing(session.client("bedrock-agentcore-control")))
        self.stub = self.stack.enter_context(Stubber(client))
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        self.session = Mock(
            region_name=REGION, client={"sts": self.sts, "bedrock-agentcore-control": client}.__getitem__
        )

    def provision(self):
        self.stub.add_response(
            "create_gateway",
            G,
            {
                "name": NAME,
                "roleArn": ROLE,
                "protocolType": "MCP",
                "protocolConfiguration": {"mcp": {"supportedVersions": ["2025-03-26"], "searchType": "SEMANTIC"}},
                "authorizerType": "CUSTOM_JWT",
                "authorizerConfiguration": AUTH,
            },
        )
        self.stub.add_response("get_gateway", G, {"gatewayIdentifier": GID})
        self.stub.add_response(
            "create_gateway_target",
            T,
            {"name": "aws-knowledge", "gatewayIdentifier": GID, "targetConfiguration": TARGET_CONFIG},
        )
        self.stub.add_response("get_gateway_target", T, {"gatewayIdentifier": GID, "targetId": TARGET})
        gateway.create(self.session, ROLE, POOL, "fixtureclient", SCOPE)
        self.stub.assert_no_pending_responses()
        return json.loads(gateway.STATE.read_text())

    def test_create_and_cleanup(self):
        state = self.provision()
        config = json.loads(gateway.MCP_CONFIG.read_text())["mcpServers"][NAME]
        self.assertEqual(
            config, {"type": "http", "url": URL, "headers": {"Authorization": "Bearer ${CLAUDE_GATEWAY_ACCESS_TOKEN}"}}
        )
        self.stub.add_response("get_gateway", G)
        self.stub.add_response("list_gateway_targets", {"items": []})
        self.stub.add_response("get_gateway_target", T)
        self.stub.add_response(
            "delete_gateway_target",
            {"gatewayArn": ARN, "targetId": TARGET, "status": "DELETING"},
            {"gatewayIdentifier": GID, "targetId": TARGET},
        )
        self.stub.add_client_error("get_gateway_target", "ResourceNotFoundException")
        self.stub.add_response("delete_gateway", {"gatewayId": GID, "status": "DELETING"}, {"gatewayIdentifier": GID})
        self.stub.add_client_error("get_gateway", "ResourceNotFoundException")
        gateway.cleanup(state, self.session)
        self.stub.assert_no_pending_responses()
        self.assertFalse(gateway.STATE.exists())

    def test_wrong_account_and_extra_targets_do_not_delete(self):
        state = self.provision()
        self.sts.get_caller_identity.return_value = {"Account": "222233334444"}
        with self.assertRaisesRegex(RuntimeError, "Wrong account"):
            gateway.cleanup(state, self.session)
        self.sts.get_caller_identity.return_value = {"Account": ACCOUNT}
        self.stub.add_response("get_gateway", G)
        self.stub.add_response("list_gateway_targets", {"items": [], "nextToken": "fixture"})
        with self.assertRaisesRegex(RuntimeError, "unrecorded"):
            gateway.cleanup(state, self.session)
        self.assertTrue(gateway.STATE.exists())
        self.stub.assert_no_pending_responses()

    def test_existing_config_and_pending_creation_refused(self):
        gateway.MCP_CONFIG.write_text("original")
        with self.assertRaises(FileExistsError):
            gateway.create(self.session, ROLE, POOL, "fixtureclient", SCOPE)
        self.assertEqual(gateway.MCP_CONFIG.read_text(), "original")
        self.assertFalse(gateway.STATE.exists())
        gateway.MCP_CONFIG.unlink()
        self.stub.add_client_error("create_gateway", "ConflictException")
        with self.assertRaises(gateway.ClientError):
            gateway.create(self.session, ROLE, POOL, "fixtureclient", SCOPE)
        state = json.loads(gateway.STATE.read_text())
        self.assertEqual(state["pending"], "gateway")
        with self.assertRaisesRegex(RuntimeError, "Unconfirmed"):
            gateway.cleanup(state, self.session)
        with self.assertRaises(FileExistsError):
            gateway.create(self.session, ROLE, POOL, "fixtureclient", SCOPE)
        self.stub.assert_no_pending_responses()

    def test_dynatrace_uses_only_the_approved_tenant_provider_and_scopes(self):
        provider = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:token-vault/default/oauth2credentialprovider/fixture"
        with patch.dict(os.environ, DYNATRACE_TENANT_ID="example", DYNATRACE_PROVIDER_ARN=provider):
            options = gateway.target_config("dynatrace", REGION, ACCOUNT)
            self.assertEqual(
                options["targetConfiguration"]["mcp"]["mcpServer"]["endpoint"],
                "https://example.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp",
            )
            auth = options["credentialProviderConfigurations"]
            self.assertEqual(
                auth,
                [
                    {
                        "credentialProviderType": "OAUTH",
                        "credentialProvider": {
                            "oauthCredentialProvider": {
                                "providerArn": provider,
                                "grantType": "CLIENT_CREDENTIALS",
                                "scopes": ["mcp-gateway:servers:read", "mcp-gateway:servers:invoke"],
                            }
                        },
                    }
                ],
            )
            # Exercise the real botocore serializer for this separate target shape.
            response = dict(
                T,
                name=options["name"],
                targetConfiguration=options["targetConfiguration"],
                credentialProviderConfigurations=auth,
            )
            params = dict(gatewayIdentifier=GID, **options)
            self.stub.add_response("create_gateway_target", response, params)
            self.session.client("bedrock-agentcore-control").create_gateway_target(**params)
            self.stub.assert_no_pending_responses()
            with self.assertRaises(ValueError):
                gateway.target_config("dynatrace", "us-west-2", ACCOUNT)
            with self.assertRaises(ValueError):
                gateway.target_config("dynatrace", REGION, "222233334444")
            for invalid in ("https://other.invalid", "foo.bar", "a/b", "foo@other"):
                with patch.dict(os.environ, DYNATRACE_TENANT_ID=invalid), self.assertRaises(ValueError):
                    gateway.create(self.session, ROLE, POOL, "fixtureclient", SCOPE, "dynatrace")
                self.assertFalse(gateway.STATE.exists())

    def test_url_validation(self):
        for url in [
            URL.replace("https:", "http:"),
            URL + "?token=x",
            URL.replace(GID, "other"),
            URL.replace("amazonaws.com", "amazonaws.com.invalid"),
        ]:
            with self.assertRaises(ValueError):
                gateway.checked_url(url, REGION, GID)

    def test_token_is_not_logged_and_redirect_is_not_followed(self):
        response = Mock(status_code=200)
        response.json.return_value = {"access_token": "fixture-token"}
        with (
            patch.object(gateway.sys.stdin, "isatty", return_value=True),
            patch.object(gateway, "getpass", return_value="fixture-secret"),
            redirect_stdout(io.StringIO()) as output,
            patch.object(gateway.requests, "post", return_value=response) as post,
        ):
            self.assertEqual(gateway.token(REGION, "demo", "client", SCOPE), "fixture-token")
            post.assert_called_once_with(
                "https://demo.auth.us-east-1.amazoncognito.com/oauth2/token",
                auth=("client", "fixture-secret"),
                data={"grant_type": "client_credentials", "scope": SCOPE},
                timeout=15,
                allow_redirects=False,
            )
            response.status_code = 307
            with self.assertRaisesRegex(RuntimeError, "refused"):
                gateway.token(REGION, "demo", "client", SCOPE)
            self.assertEqual(output.getvalue(), "")
