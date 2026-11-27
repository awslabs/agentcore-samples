import threading
import unittest
from http.client import HTTPConnection
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import boto3
import delegated_consent as demo
from botocore.stub import ANY, Stubber

SESSION = "urn:ietf:params:oauth:request_uri:fixture"
URL = "https://consent.invalid/start"


class Consent(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client(
            "bedrock-agentcore", region_name="us-east-1", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        self.stub = self.enterContext(Stubber(self.client))
        self.addCleanup(self.stub.assert_no_pending_responses)

    def begin(self, resource, response):
        self.stub.add_response(
            "get_workload_access_token_for_jwt",
            {"workloadAccessToken": "workload-fixture"},
            {"workloadName": "example", "userToken": "inbound-fixture"},
        )
        params = {
            "resourceCredentialProviderName": "provider",
            "workloadIdentityToken": "workload-fixture",
            "oauth2Flow": "USER_FEDERATION",
            "scopes": demo.SCOPES[resource],
            "resourceOauth2ReturnUrl": demo.RETURN_URL,
            "customState": ANY,
        }
        self.stub.add_response("get_resource_oauth2_token", response, params)
        return params

    def test_cli_client_lifecycle(self):
        client = boto3.client(
            "bedrock-agentcore", region_name="us-west-2", aws_access_key_id="fixture", aws_secret_access_key="fixture"
        )
        sts = Mock()
        sts.get_caller_identity.return_value = {"Account": "fixture"}
        with (
            patch(
                "sys.argv",
                [
                    "consent",
                    "github",
                    "--workload",
                    "work",
                    "--provider",
                    "provider",
                    "--region",
                    "us-west-2",
                    "--account",
                    "fixture",
                ],
            ),
            patch.object(demo.boto3, "Session", return_value=Mock(client=Mock(side_effect=[sts, client]))),
            patch.object(demo.getpass, "getpass", return_value="fixture"),
            patch.object(demo, "consent", return_value="completed") as flow,
            patch.object(client, "close", wraps=client.close) as close,
        ):
            demo.main()
        self.assertIs(flow.call_args.args[0], client)
        close.assert_called_once_with()

    def test_cached(self):
        self.begin("github", {"accessToken": "hidden"})
        visit = Mock(side_effect=AssertionError("no callback required"))
        self.assertEqual(
            demo.consent(self.client, "example", "provider", "github", "inbound-fixture", visit),
            "Existing user grant available",
        )
        visit.assert_not_called()

    def test_new_grant(self):
        params = self.begin("calendar", {"authorizationUrl": URL, "sessionUri": SESSION})
        self.stub.add_response(
            "complete_resource_token_auth",
            {},
            {"userIdentifier": {"userToken": "inbound-fixture"}, "sessionUri": SESSION},
        )
        self.stub.add_response(
            "get_resource_oauth2_token", {"accessToken": "hidden"}, {**params, "sessionUri": SESSION}
        )
        visit = Mock()
        self.assertEqual(
            demo.consent(self.client, "example", "provider", "calendar", "inbound-fixture", visit),
            "User grant available after consent",
        )
        self.assertEqual(visit.call_args.args[:2], (URL, SESSION))

    def test_failed_callback_is_not_completed(self):
        self.begin("github", {"authorizationUrl": URL, "sessionUri": SESSION})
        with self.assertRaises(TimeoutError):
            demo.consent(
                self.client, "example", "provider", "github", "inbound-fixture", Mock(side_effect=TimeoutError)
            )

    def test_real_loopback_callback(self):
        responses = []

        def send():
            for session, state in [(SESSION, "wrong"), ("wrong", "fixture-state"), (SESSION, "fixture-state")]:
                conn = HTTPConnection("127.0.0.1", demo.PORT, timeout=3)
                try:
                    conn.request("GET", "/callback?" + urlencode({"session_id": session, "state": state}))
                    res = conn.getresponse()
                    responses.append(res.status)
                    res.read()
                finally:
                    conn.close()

        browser = threading.Thread(target=send)
        with patch.object(demo.webbrowser, "open", side_effect=lambda url: browser.start() or True):
            demo.local_visit(URL, SESSION, "fixture-state")
        browser.join(timeout=3)
        self.assertEqual(responses, [400, 400, 200])
