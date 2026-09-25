"""Thin OpenAI Agents SDK adapter for AgentCore Payments."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
GetRequest = Callable[[httpx.URL, str, dict[str, str] | None], Any]
Resolver = Callable[[str, int], Sequence[str]]


def _http_get(url: httpx.URL, address: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Connect to the validated address while verifying TLS for the merchant."""
    with httpx.Client(verify=True, trust_env=False, follow_redirects=False, timeout=30.0) as client:
        return client.get(
            url.copy_with(host=address),
            headers={**(headers or {}), "Host": url.netloc.decode("ascii")},
            extensions={"sni_hostname": url.host},
        )


def _resolve(hostname: str, port: int) -> Sequence[str]:
    return [entry[4][0] for entry in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)]


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


@dataclass(frozen=True)
class PaymentConfig:
    manager_arn: str
    instrument_id: str
    session_id: str
    user_id: str
    allowed_hosts: frozenset[str]
    region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> PaymentConfig:
        env_names = {
            "manager_arn": "PAYMENT_MANAGER_ARN",
            "instrument_id": "PAYMENT_INSTRUMENT_ID",
            "session_id": "PAYMENT_SESSION_ID",
            "user_id": "PAYMENT_USER_ID",
        }
        values = {field: os.getenv(name, "").strip() for field, name in env_names.items()}
        missing = [env_names[field] for field, value in values.items() if not value]
        if missing:
            raise ValueError("Missing payment configuration: " + ", ".join(missing))

        allowed_hosts = frozenset(
            host.strip().lower().rstrip(".")
            for host in os.getenv("PAID_RESEARCH_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        if not allowed_hosts:
            raise ValueError("PAID_RESEARCH_ALLOWED_HOSTS must contain an exact host")

        return cls(
            **values,
            allowed_hosts=allowed_hosts,
            region=payment_region(values["manager_arn"]),
        )


class X402PaymentClient:
    """Fetch one approved source through a budget-bounded payment session."""

    def __init__(
        self,
        config: PaymentConfig,
        payment_manager: Any,
        *,
        get: GetRequest = _http_get,
        resolver: Resolver = _resolve,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.payment_manager = payment_manager
        self.get = get
        self.resolver = resolver
        self.token_factory = token_factory or (lambda: str(uuid.uuid4()))
        self._results: dict[str, str] = {}
        # PaymentManager is not thread-safe. Also prevent repeated tool calls
        # from signing for the same source twice during one research run.
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> X402PaymentClient:
        config = PaymentConfig.from_env()
        return cls(config, create_payment_manager(config.manager_arn))

    @staticmethod
    def _json(**values: Any) -> str:
        return json.dumps(values, default=str, sort_keys=True)

    def _validate_url(self, url: str) -> tuple[httpx.URL, str]:
        try:
            parsed = httpx.URL(url)
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
        if hostname not in self.config.allowed_hosts:
            raise ValueError(f"Host is not approved for paid research: {hostname}")

        try:
            addresses = self.resolver(hostname, port)
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

    def fetch(self, url: str) -> str:
        """Fetch once per source per run, retaining successes and terminal failures."""
        with self._lock:
            if url not in self._results:
                self._results[url] = self._fetch_once(url)
            return self._results[url]

    def _fetch_once(self, url: str) -> str:
        """Make at most one signing attempt and one GET with the resulting proof."""
        try:
            parsed, address = self._validate_url(url)
        except ValueError as error:
            return self._json(ok=False, source_url=url, error=str(error), payment_made=False, payment_attempts=0)

        try:
            response = self.get(parsed, address, None)
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
                payment_instrument_id=self.config.instrument_id,
                payment_session_id=self.config.session_id,
                user_id=self.config.user_id,
                client_token=self.token_factory(),
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
            response = self.get(parsed, address, payment_header)
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
                payment_session_id=self.config.session_id,
                user_id=self.config.user_id,
            )
        maximum = session.get("limits", {}).get("maxSpendAmount", {})
        available = session.get("availableLimits", {}).get("availableSpendAmount", {})
        return self._json(
            maximum_spend=maximum.get("value"),
            available_spend=available.get("value"),
            currency=available.get("currency") or maximum.get("currency"),
            expiry_time_in_minutes=session.get("expiryTimeInMinutes"),
        )
