"""Signed AgentCore Policy Gateway authorization client."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from buyer.core import PaymentRequirement, PolicyDenied


class _NoRedirect(HTTPRedirectHandler):
    """Fail closed rather than forwarding signed Gateway headers to a redirect."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


_GATEWAY_OPENER = build_opener(_NoRedirect()).open


def _validate_gateway_url(gateway_url: str) -> None:
    """Require a direct HTTPS Policy Gateway endpoint without URL credentials."""

    parsed = urlsplit(gateway_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "policy_gateway_url must be an absolute HTTPS URL without embedded credentials"
        )


@dataclass(frozen=True)
class GatewayPolicyContext:
    """Configuration for one Policy Gateway authorization request."""

    gateway_url: str
    target_name: str
    policy_session_id: str
    region: str


class GatewayPolicyAuthorizer:
    """Call a Policy Gateway target before payment processing."""

    def __init__(self, context: GatewayPolicyContext) -> None:
        _validate_gateway_url(context.gateway_url)
        self._context = context

    def authorize(self, requirement: PaymentRequirement) -> None:
        """Raise PolicyDenied unless the Gateway returns AUTHORIZED."""

        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {
                    "name": f"{self._context.target_name}___authorize_payment",
                    "arguments": requirement.policy_input(),
                },
            }
        ).encode("utf-8")
        credentials = boto3.Session().get_credentials()
        if credentials is None:
            raise RuntimeError("No AWS credentials are available for Policy Gateway signing")

        signed = AWSRequest(
            method="POST",
            url=self._context.gateway_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "x-amzn-bedrock-agentcore-policy-session-id": self._context.policy_session_id,
            },
        )
        SigV4Auth(
            credentials.get_frozen_credentials(),
            "bedrock-agentcore",
            self._context.region,
        ).add_auth(signed)
        request = Request(
            self._context.gateway_url,
            data=payload,
            headers=dict(signed.prepare().headers),
            method="POST",
        )
        try:
            with _GATEWAY_OPENER(request, timeout=20) as response:
                decision = self._decision(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code == 403:
                raise PolicyDenied("POLICY_DENY: Gateway denied the payment intent") from error
            raise RuntimeError(f"Policy Gateway request failed with HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError("Unable to reach the Policy Gateway") from error

        if decision.get("decision") != "AUTHORIZED":
            raise PolicyDenied("POLICY_DENY: Gateway did not authorize the payment intent")

    @staticmethod
    def _decision(response_body: str) -> dict[str, Any]:
        """Extract the target's JSON decision from a JSON-RPC tools/call result."""

        try:
            response = json.loads(response_body)
        except json.JSONDecodeError as error:
            raise PolicyDenied("POLICY_DENY: Gateway returned a malformed response") from error

        if not isinstance(response, dict):
            raise PolicyDenied("POLICY_DENY: Gateway returned a malformed response")

        result = response.get("result", {})
        if not isinstance(result, dict):
            return {}

        content = result.get("content", [])
        if not isinstance(content, list):
            return result

        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if text:
                try:
                    decision = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decision, dict):
                    return decision
        return result
