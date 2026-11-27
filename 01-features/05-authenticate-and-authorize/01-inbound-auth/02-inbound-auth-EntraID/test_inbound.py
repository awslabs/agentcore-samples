import unittest
from unittest.mock import Mock
from urllib.parse import unquote

import check_inbound as check

REGION, ACCOUNT, RID = "us-east-1", "111111111111", "example-0123456789"
TENANT = "12345678-1234-1234-1234-123456789012"
ARN = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:runtime/{RID}"
CFG = {
    "agentRuntimeArn": ARN,
    "status": "READY",
    "authorizerConfiguration": {
        "customJWTAuthorizer": {
            "allowedAudience": ["api://example"],
            "discoveryUrl": f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration",
        }
    },
}


class Inbound(unittest.TestCase):
    def test_destination(self):
        host, path = check.endpoint(
            Mock(get_agent_runtime=Mock(return_value=CFG)), REGION, ACCOUNT, RID, TENANT, "api://example"
        )
        self.assertEqual(host, "bedrock-agentcore.us-east-1.amazonaws.com")
        self.assertEqual(unquote(path), f"/runtimes/{ARN}/invocations?qualifier=DEFAULT")
        for cfg in [
            dict(CFG, status="CREATING"),
            dict(CFG, authorizerConfiguration={}),
            dict(CFG, agentRuntimeArn=ARN.replace(ACCOUNT, "222222222222")),
        ]:
            with self.assertRaises(ValueError):
                check.endpoint(
                    Mock(get_agent_runtime=Mock(return_value=cfg)), REGION, ACCOUNT, RID, TENANT, "api://example"
                )

    def test_negative_before_bearer(self):
        for status in [200, 302, 500]:
            conn = Mock(getresponse=Mock(return_value=Mock(status=status)))
            get = Mock()
            with self.assertRaises(RuntimeError):
                check.probe("fixture.invalid", "/fixture", get, Mock(return_value=conn))
            get.assert_not_called()
            conn.close.assert_called_once()

    def test_success(self):
        conns = [Mock(getresponse=Mock(return_value=Mock(status=n))) for n in [403, 200]]
        self.assertIn(
            "HTTP 200", check.probe("fixture.invalid", "/fixture", lambda: "fixture.jwt.value", Mock(side_effect=conns))
        )
        self.assertNotIn("Authorization", conns[0].request.call_args.kwargs["headers"])
        self.assertEqual(conns[1].request.call_args.kwargs["headers"]["Authorization"], "Bearer fixture.jwt.value")
        for conn in conns:
            conn.close.assert_called_once()
