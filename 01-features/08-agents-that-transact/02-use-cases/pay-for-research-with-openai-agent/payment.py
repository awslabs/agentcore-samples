"""Thin OpenAI Agents SDK adapter for AgentCore Payments."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import uuid
from typing import Any

import httpx
from bedrock_agentcore.payments import PaymentManager
from bedrock_agentcore.payments.manager import (
    InsufficientBudget,
    InvalidPaymentInstrument,
    PaymentError,
    PaymentInstrumentNotFound,
    PaymentSessionExpired,
    PaymentSessionNotFound,
)
from botocore.config import Config
from botocore.exceptions import BotoCoreError

MAX_BODY_CHARS = 100_000


def _http_get(url: httpx.URL, address: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Connect to the validated address while verifying TLS for the merchant."""
    with httpx.Client(verify=True, trust_env=False, follow_redirects=False, timeout=30.0) as client:
        return client.get(
            url.copy_with(host=address),
            headers={**(headers or {}), "Host": url.netloc.decode("ascii")},
            extensions={"sni_hostname": url.host},
        )


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Set {name} in .env before using payments")
    return value


def payment_region(manager_arn: str) -> str:
    """Use the manager's region, independently of the Bedrock model region."""
    parts = manager_arn.split(":", 5)
    if (
        len(parts) != 6
        or parts[0] != "arn"
        or parts[2] != "bedrock-agentcore"
        or not parts[3]
        or not parts[5].startswith("payment-manager/")
    ):
        raise ValueError("PAYMENT_MANAGER_ARN must be an AgentCore payment-manager ARN")
    return parts[3]


def create_payment_manager(manager_arn: str) -> PaymentManager:
    """Create a GA SDK client with bounded timeouts and standard AWS retries."""
    return PaymentManager(
        payment_manager_arn=manager_arn,
        region_name=payment_region(manager_arn),
        boto_client_config=Config(
            connect_timeout=10,
            read_timeout=30,
            retries={"mode": "standard", "total_max_attempts": 2},
        ),
    )


class X402PaymentClient:
    """Fetch one application-bound source through a budget-bounded session."""

    def __init__(self, url: str) -> None:
        self.url = url
        manager_arn = _required_setting("PAYMENT_MANAGER_ARN")
        self.instrument_id = _required_setting("PAYMENT_INSTRUMENT_ID")
        self.session_id = _required_setting("PAYMENT_SESSION_ID")
        self.user_id = _required_setting("PAYMENT_USER_ID")
        self.allowed_hosts = {
            host.strip().lower().rstrip(".")
            for host in os.getenv("PAID_RESEARCH_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        }
        if not self.allowed_hosts:
            raise ValueError("PAID_RESEARCH_ALLOWED_HOSTS must contain an exact host")

        self.payment_manager = create_payment_manager(manager_arn)
        self._result: str | None = None
        # PaymentManager is not thread-safe. Also prevent repeated tool calls
        # from signing for the same source twice during one research run.
        self._lock = threading.Lock()

    @staticmethod
    def _json(**values: Any) -> str:
        return json.dumps(values, default=str, sort_keys=True)

    def _validate_url(self) -> tuple[httpx.URL, str]:
        try:
            parsed = httpx.URL(self.url)
        except httpx.InvalidURL as error:
            raise ValueError("Invalid payment URL") from error
        if parsed.scheme != "https" or not parsed.host:
            raise ValueError("Paid research requires an HTTPS URL with a hostname")
        if parsed.userinfo or "%" in parsed.host:
            raise ValueError("URL credentials and scoped IP addresses are not allowed")
        port = parsed.port if parsed.port is not None else 443
        if not 1 <= port <= 65535:
            raise ValueError("URL port must be between 1 and 65535")

        hostname = parsed.host.lower().rstrip(".")
        if hostname not in self.allowed_hosts:
            raise ValueError(f"Host is not approved for paid research: {hostname}")

        try:
            addresses = [entry[4][0] for entry in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)]
        except OSError as error:
            raise ValueError("Could not resolve the merchant hostname") from error
        if not addresses:
            raise ValueError("Merchant hostname resolved to no addresses")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                not ip.is_global
                or ip.is_multicast
                or ip.is_reserved
                or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
            ):
                raise ValueError("Merchant hostname resolves to a private or non-routable address")
        address = next((address for address in addresses if ipaddress.ip_address(address).version == 4), addresses[0])
        return parsed, address

    def fetch(self) -> str:
        """Fetch the bound source once, retaining successes and terminal failures."""
        with self._lock:
            if self._result is None:
                self._result = self._fetch_once()
            return self._result

    def _fetch_once(self) -> str:
        """Make at most one signing attempt and one GET with the resulting proof."""
        url = self.url
        try:
            parsed, address = self._validate_url()
        except ValueError as error:
            return self._json(ok=False, source_url=url, error=str(error), payment_made=False, payment_attempts=0)

        try:
            response = _http_get(parsed, address)
        except httpx.RequestError:
            return self._json(
                ok=False,
                source_url=url,
                error="Initial merchant request failed",
                payment_made=False,
                payment_attempts=0,
            )

        if response.status_code != 402:
            return self._json(
                ok=200 <= response.status_code < 300,
                source_url=url,
                status_code=response.status_code,
                body=response.text[:MAX_BODY_CHARS],
                payment_made=False,
                payment_attempts=0,
            )

        try:
            payment_header = self.payment_manager.generate_payment_header(
                payment_instrument_id=self.instrument_id,
                payment_session_id=self.session_id,
                user_id=self.user_id,
                client_token=str(uuid.uuid4()),
                payment_required_request={
                    "statusCode": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text,
                },
            )
            if not payment_header:
                raise PaymentError("AgentCore returned an empty payment header")
        except (
            InsufficientBudget,
            InvalidPaymentInstrument,
            PaymentInstrumentNotFound,
            PaymentSessionExpired,
            PaymentSessionNotFound,
        ) as error:
            return self._json(
                ok=False,
                source_url=url,
                status_code=402,
                error=f"Payment rejected: {type(error).__name__}",
                payment_made=False,
                payment_attempts=1,
            )
        except (PaymentError, BotoCoreError):
            return self._json(
                ok=False,
                source_url=url,
                error="Payment proof generation failed; inspect the session before trying again",
                payment_made=None,
                payment_attempts=1,
            )

        try:
            response = _http_get(parsed, address, payment_header)
        except httpx.RequestError:
            return self._json(
                ok=False,
                source_url=url,
                error="Merchant request failed after proof generation; payment outcome is unknown. Do not retry.",
                payment_made=None,
                payment_attempts=1,
            )

        accepted = 200 <= response.status_code < 300
        result = {
            "ok": accepted,
            "source_url": url,
            "status_code": response.status_code,
            "body": response.text[:MAX_BODY_CHARS],
            "payment_made": True if accepted else None,
            "payment_attempts": 1,
        }
        if not accepted:
            result["error"] = (
                "Merchant did not return paid content; payment outcome is unknown. "
                "Inspect the session before trying again."
            )
        return self._json(**result)

    def session_status(self) -> str:
        """Return budget status without exposing wallet or session identifiers."""
        with self._lock:
            session = self.payment_manager.get_payment_session(
                payment_session_id=self.session_id,
                user_id=self.user_id,
            )
        maximum = session.get("limits", {}).get("maxSpendAmount", {})
        available = session.get("availableLimits", {}).get("availableSpendAmount", {})
        return self._json(
            maximum_spend=maximum.get("value"),
            available_spend=available.get("value"),
            currency=available.get("currency") or maximum.get("currency"),
            expiry_time_in_minutes=session.get("expiryTimeInMinutes"),
        )
