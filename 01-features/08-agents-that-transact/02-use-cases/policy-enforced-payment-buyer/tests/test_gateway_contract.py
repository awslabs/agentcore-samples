from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from botocore.credentials import ReadOnlyCredentials


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import PaymentRequirement, PolicyDenied  # noqa: E402
from buyer.gateway import GatewayPolicyAuthorizer, GatewayPolicyContext  # noqa: E402


_TEST_ACCESS_KEY = "test-access-key"
_TEST_CREDENTIAL = "unit-test-credential-placeholder"


class Credentials:
    def get_frozen_credentials(self) -> ReadOnlyCredentials:
        return ReadOnlyCredentials(
            access_key=_TEST_ACCESS_KEY,
            secret_key=_TEST_CREDENTIAL,
            token=None,
        )


class Session:
    def get_credentials(self) -> Credentials:
        return Credentials()


class Response:
    def __init__(self, body: dict[str, object]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class RawResponse:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> RawResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class GatewayPolicyAuthorizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.requirement = PaymentRequirement(
            resource_url="https://seller.example/premium",
            pay_to="0x1111111111111111111111111111111111111111",
            amount=1000,
            network="eip155:84532",
            asset="0x2222222222222222222222222222222222222222",
            payment_required_request={},
        )
        self.authorizer = GatewayPolicyAuthorizer(
            GatewayPolicyContext(
                gateway_url="https://gateway.example/mcp",
                target_name="PaymentPolicyTools",
                policy_session_id="test-session",
                region="us-west-2",
            )
        )

    @patch("buyer.gateway._GATEWAY_OPENER")
    @patch("buyer.gateway.boto3.Session", return_value=Session())
    def test_authorized_gateway_response_is_accepted(self, session: object, opener: object) -> None:
        del session
        opener.return_value = Response(
            {
                "result": {
                    "content": [
                        {"type": "text", "text": '{"decision": "AUTHORIZED"}'},
                    ]
                }
            }
        )

        self.authorizer.authorize(self.requirement)

        request_body = json.loads(opener.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(
            request_body["params"]["name"],
            "PaymentPolicyTools___authorize_payment",
        )
        self.assertEqual(
            request_body["params"]["arguments"],
            self.requirement.policy_input(),
        )

    @patch("buyer.gateway._GATEWAY_OPENER")
    @patch("buyer.gateway.boto3.Session", return_value=Session())
    def test_non_authorized_gateway_response_is_denied(
        self, session: object, opener: object
    ) -> None:
        del session
        opener.return_value = Response(
            {
                "result": {
                    "content": [
                        {"type": "text", "text": '{"decision": "DENIED"}'},
                    ]
                }
            }
        )

        with self.assertRaisesRegex(PolicyDenied, "did not authorize"):
            self.authorizer.authorize(self.requirement)

    @patch("buyer.gateway._GATEWAY_OPENER")
    @patch("buyer.gateway.boto3.Session", return_value=Session())
    def test_gateway_server_error_is_not_treated_as_policy_denial(
        self, session: object, opener: object
    ) -> None:
        del session
        opener.side_effect = HTTPError(
            url="https://gateway.example/mcp",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            self.authorizer.authorize(self.requirement)

    @patch("buyer.gateway._GATEWAY_OPENER")
    @patch("buyer.gateway.boto3.Session", return_value=Session())
    def test_malformed_gateway_response_is_denied(self, session: object, opener: object) -> None:
        del session
        opener.return_value = RawResponse("not-json")

        with self.assertRaisesRegex(PolicyDenied, "malformed response"):
            self.authorizer.authorize(self.requirement)

    @patch("buyer.gateway._GATEWAY_OPENER")
    @patch("buyer.gateway.boto3.Session", return_value=Session())
    def test_non_json_content_item_is_denied(self, session: object, opener: object) -> None:
        del session
        opener.return_value = Response(
            {
                "result": {
                    "content": [
                        {"type": "text", "text": "not-json"},
                    ]
                }
            }
        )

        with self.assertRaisesRegex(PolicyDenied, "did not authorize"):
            self.authorizer.authorize(self.requirement)

    def test_gateway_url_must_be_direct_https(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTPS"):
            GatewayPolicyAuthorizer(
                GatewayPolicyContext(
                    gateway_url="http://gateway.example/mcp",
                    target_name="PaymentPolicyTools",
                    policy_session_id="test-session",
                    region="us-west-2",
                )
            )

        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            GatewayPolicyAuthorizer(
                GatewayPolicyContext(
                    gateway_url="https://user:password@gateway.example/mcp",
                    target_name="PaymentPolicyTools",
                    policy_session_id="test-session",
                    region="us-west-2",
                )
            )
