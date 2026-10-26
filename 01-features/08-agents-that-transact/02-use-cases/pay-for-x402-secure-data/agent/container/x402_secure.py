"""Direct API client for the t54 x402-secure trust endpoint (plugin-compatible 402 shape)."""

from __future__ import annotations

import json
import os
from typing import Any

import requests

DEFAULT_X402_SECURE_BASE_URL = "https://x402-secure-api.t54.ai"
DEFAULT_X402_SECURE_SCORE_ENDPOINT = "/x402/tools/get_overall_score"

# AgentCorePaymentsPlugin's GenericPaymentHandler detects a 402 only when the tool
# result text begins with this marker followed by a JSON object shaped as
# {"statusCode": 402, "headers": {...}, "body": {...}} (the "402 PaymentRequired
# Standard Response Structure Specification v1.0"). statusCode is camelCase; headers
# and body must both be dicts, or the handler ignores the result and skips payment.
PAYMENT_REQUIRED_MARKER = "PAYMENT_REQUIRED: "


def resolve_x402_secure_base_url() -> str:
    return os.environ.get("X402_SECURE_BASE_URL", DEFAULT_X402_SECURE_BASE_URL)


def resolve_x402_secure_score_endpoint() -> str:
    return os.environ.get(
        "X402_SECURE_SCORE_ENDPOINT",
        DEFAULT_X402_SECURE_SCORE_ENDPOINT,
    )


def payment_required_result(response: requests.Response) -> str:
    """Shape an HTTP 402 response into the marker format AgentCorePaymentsPlugin detects.

    The plugin's GenericPaymentHandler recognizes a payment-required tool result only
    when its text starts with the PAYMENT_REQUIRED marker followed by a JSON object with
    ``statusCode`` (int), ``headers`` (dict), and ``body`` (dict). On retry the plugin
    injects the ``X-PAYMENT`` header into the tool input's ``headers``.

    Args:
        response: The 402 response from an x402 endpoint.

    Returns:
        A ``PAYMENT_REQUIRED: <json>`` string carrying the 402 status, headers, and body.
    """
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {"data": body}
    payload = {
        "statusCode": response.status_code,
        "headers": dict(response.headers),
        "body": body,
    }
    return PAYMENT_REQUIRED_MARKER + json.dumps(payload)


def is_payment_required(result: Any) -> bool:
    """Return True if a tool result is a PAYMENT_REQUIRED marker string."""
    return isinstance(result, str) and result.startswith(PAYMENT_REQUIRED_MARKER)


class X402SecureClient:
    """Plugin-compatible direct API client for t54 x402-secure trust endpoints."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        score_endpoint: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 60,
    ) -> None:
        resolved_base_url = base_url or resolve_x402_secure_base_url()
        resolved_score_endpoint = score_endpoint or resolve_x402_secure_score_endpoint()
        self.base_url = resolved_base_url.rstrip("/")
        self.score_endpoint_path = (
            resolved_score_endpoint if resolved_score_endpoint.startswith("/") else f"/{resolved_score_endpoint}"
        )
        self.session = session or requests.Session()
        self.timeout = timeout

    def call_tool(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | str:
        """POST to an x402-secure tool endpoint.

        Returns:
            The parsed JSON result, or a PAYMENT_REQUIRED marker string for plugin retry.

        Raises:
            RuntimeError: For non-2xx, non-402 responses.
        """
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        url = f"{self.base_url}{path}"
        response = self.session.post(
            url,
            json=body,
            headers=headers,
            timeout=self.timeout,
        )

        if response.status_code == 402:
            return payment_required_result(response)

        if response.status_code >= 400:
            raise RuntimeError(f"x402-secure score check failed: {response.status_code} {response.text[:500]}")
        return response.json()

    def score_endpoint(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | str:
        """Request the overall trust score for a target endpoint URL."""
        return self.call_tool(
            self.score_endpoint_path,
            {"url": url},
            headers=headers,
        )
