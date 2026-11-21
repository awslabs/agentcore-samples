"""AgentCore Payments adapter for OpenAI Agents SDK x402 tools."""

import base64
import ipaddress
import json
import socket
import uuid
from collections.abc import Callable
from typing import Literal

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
from botocore.exceptions import BotoCoreError

# (url, first payment requirement from the 402) -> (allow, reason). Runs after the merchant's 402 and before any
# payment proof is generated, so a deny never touches the payment session.
PrePaymentCheck = Callable[[str, dict], tuple[bool, str]]


def _resolve_url(url: str) -> tuple[httpx.URL, str]:
    """Validate an HTTPS destination and return a public IP to connect to."""
    try:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or not parsed.host:
            raise ValueError("Only absolute HTTPS URLs are supported for payment requests")
        if parsed.userinfo or "%" in parsed.host:
            raise ValueError("URL credentials and scoped IP addresses are not supported")
        port = parsed.port if parsed.port is not None else 443
        if not 1 <= port <= 65535:
            raise ValueError("URL port must be between 1 and 65535")
    except httpx.InvalidURL as error:
        raise ValueError("Invalid payment URL") from error

    try:
        addresses = socket.getaddrinfo(parsed.host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Cannot resolve hostname") from error
    if not addresses:
        raise ValueError("Cannot resolve hostname")

    for _family, _, _, _, socket_address in addresses:
        ip = ipaddress.ip_address(socket_address[0])
        if (
            not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
        ):
            raise ValueError("Cannot fetch private/internal or non-public network addresses")

    # Prefer IPv4 when available; some local environments have no IPv6 route.
    address = next((a for a in addresses if a[0] == socket.AF_INET), addresses[0])
    return parsed, address[4][0]


def _request(
    url: httpx.URL,
    address: str,
    method: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Pin the connection IP while verifying TLS against the original host."""
    # A new client avoids carrying challenge-response cookies into the paid GET.
    # Proxies and redirects could bypass address validation or forward the proof.
    with httpx.Client(verify=True, trust_env=False, follow_redirects=False, timeout=30) as client:
        return client.request(
            method,
            url.copy_with(host=address),
            headers={**(headers or {}), "Host": url.netloc.decode("ascii")},
            extensions={"sni_hostname": url.host},
        )


def _first_requirement(response: httpx.Response) -> dict:
    """The first entry of the 402's accepts[]: x402 v2 PAYMENT-REQUIRED header (base64 JSON), else a v1 JSON body."""
    candidates = []
    header = response.headers.get("payment-required")
    if header:
        try:
            candidates.append(json.loads(base64.b64decode(header + "=" * (-len(header) % 4))))
        except (ValueError, TypeError):
            pass
    try:
        candidates.append(response.json())
    except ValueError:
        pass
    for doc in candidates:
        accepts = doc.get("accepts") if isinstance(doc, dict) else None
        if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
            return accepts[0]
    return {}


def allowlist_check(allowed_pay_to: set[str], max_amount: int | None = None) -> PrePaymentCheck:
    """Example pre-payment check: pay only known payout wallets, and never more than max_amount (atomic units)."""
    allowed = {wallet.lower() for wallet in allowed_pay_to}

    def check(url: str, requirement: dict) -> tuple[bool, str]:
        pay_to = str(requirement.get("payTo", "")).lower()
        if pay_to not in allowed:
            return False, f"payTo {pay_to or '(missing)'} is not on the allowlist"
        amount = requirement.get("amount", requirement.get("maxAmountRequired"))
        if max_amount is not None:
            try:
                if int(amount) > max_amount:
                    return False, f"amount {amount} is above the maximum {max_amount}"
            except (TypeError, ValueError):
                return False, "the quote has no numeric amount"
        return True, "payTo allowed and amount within the maximum"

    return check


def build_x402_fetch(
    payment_manager_arn: str,
    payment_instrument_id: str,
    payment_session_id: str,
    user_id: str,
    region: str,
    pre_payment_check: PrePaymentCheck | None = None,
) -> Callable[..., str]:
    """Build an x402 fetch tool bound to one user and payment session.

    pre_payment_check, when given, is called with the URL and the 402's first payment requirement before any
    payment proof is generated. If it returns (False, reason), or raises, nothing is paid.
    """
    manager = PaymentManager(
        payment_manager_arn=payment_manager_arn,
        region_name=region,
    )

    def x402_fetch(url: str, method: Literal["GET"] = "GET") -> str:
        """Fetch a public HTTPS URL, making at most one payment attempt.

        Args:
            url: The public HTTPS endpoint to retrieve.
            method: Only GET is supported by this read-only tutorial.
        """
        if not user_id:
            return json.dumps({"error": "user_id is required; set USER_ID in .env or user_id in the invoke payload"})
        if method != "GET":
            return json.dumps({"error": "Only GET is supported"})
        try:
            parsed, address = _resolve_url(url)
        except ValueError as error:
            return json.dumps({"error": str(error), "payment_made": False, "payment_attempts": 0})

        try:
            response = _request(parsed, address, method)
        except httpx.RequestError:
            return json.dumps(
                {"error": "Initial merchant request failed", "payment_made": False, "payment_attempts": 0}
            )
        if response.status_code != 402:
            return json.dumps(
                {
                    "status_code": response.status_code,
                    "body": response.text,
                    "payment_made": False,
                    "payment_attempts": 0,
                }
            )

        if pre_payment_check is not None:
            try:
                allow, reason = pre_payment_check(str(parsed), _first_requirement(response))
            except Exception as error:  # fail closed: a broken check must never lead to a payment
                allow, reason = False, f"pre-payment check failed ({type(error).__name__})"
            if not allow:
                return json.dumps(
                    {
                        "status_code": 402,
                        "error": f"Payment blocked by pre-payment check: {reason}",
                        "payment_made": False,
                        "payment_attempts": 0,
                    }
                )

        # One idempotency key per logical payment; never mint another payment
        # merely because the merchant still returns 402 or the reply is lost.
        try:
            payment_header = manager.generate_payment_header(
                payment_instrument_id=payment_instrument_id,
                payment_session_id=payment_session_id,
                user_id=user_id,
                client_token=str(uuid.uuid4()),
                payment_required_request={
                    "statusCode": response.status_code,
                    "headers": dict(response.headers),
                    "body": response.text,
                },
            )
        except (
            InsufficientBudget,
            InvalidPaymentInstrument,
            PaymentInstrumentNotFound,
            PaymentSessionExpired,
            PaymentSessionNotFound,
        ) as error:
            return json.dumps(
                {
                    "status_code": 402,
                    "error": f"Payment rejected: {type(error).__name__}",
                    "payment_made": False,
                    "payment_attempts": 1,
                }
            )
        except (PaymentError, BotoCoreError):
            return json.dumps(
                {
                    "status_code": 402,
                    "error": "Payment proof generation failed; check the session before trying again",
                    "payment_made": None,
                    "payment_attempts": 1,
                }
            )

        try:
            retry_response = _request(parsed, address, method, payment_header)
        except httpx.RequestError:
            return json.dumps(
                {
                    "error": "Merchant request failed after proof generation; payment outcome is unknown. Do not retry.",
                    "payment_made": None,
                    "payment_attempts": 1,
                }
            )

        accepted = 200 <= retry_response.status_code < 300
        result = {
            "status_code": retry_response.status_code,
            "body": retry_response.text,
            "payment_made": True if accepted else None,
            "payment_attempts": 1,
        }
        if not accepted:
            result["error"] = (
                "Merchant did not return paid content after one payment attempt; "
                "payment outcome is unknown. Check the session before trying again."
            )
        return json.dumps(result)

    return x402_fetch
