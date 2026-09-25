"""Deterministic purchase path shared by local and Runtime buyers."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class PaymentRequirementError(ValueError):
    """Raised when a seller returns an invalid x402 requirement."""


class PolicyDenied(PermissionError):
    """Raised before payment execution when Policy rejects an intent."""


class _NoRedirect(HTTPRedirectHandler):
    """Fail closed instead of forwarding a payment header to a redirect target."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


_DEFAULT_OPENER = build_opener(_NoRedirect()).open


@dataclass(frozen=True)
class PaymentRequirement:
    """One selected x402 payment requirement bound to a resource URL."""

    resource_url: str
    pay_to: str
    amount: int
    network: str
    asset: str
    payment_required_request: dict[str, Any]

    def policy_input(self) -> dict[str, Any]:
        return {
            "resourceUrl": self.resource_url,
            "payTo": self.pay_to,
            "amount": self.amount,
            "network": self.network,
            "asset": self.asset,
        }


@dataclass(frozen=True)
class PurchaseResult:
    """Result of a completed seller retry."""

    status_code: int
    body: dict[str, Any]
    requirement: PaymentRequirement
    payment_execution: str
    settlement: str


class PolicyAuthorizer(Protocol):
    """Server-side component that authorizes an immutable payment requirement."""

    def authorize(self, requirement: PaymentRequirement) -> None:
        """Raise PolicyDenied unless the requirement is authorized."""


class PaymentExecutor(Protocol):
    """Component that turns the captured requirement into retry headers."""

    execution_label: str

    def payment_headers(self, requirement: PaymentRequirement) -> Mapping[str, str]:
        """Return payment headers for the exact requirement."""


def _decode_json(value: str) -> dict[str, Any]:
    try:
        decoded = base64.b64decode(value, validate=True)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as error:
        raise PaymentRequirementError("payment-required is not base64 JSON") from error
    if not isinstance(payload, dict):
        raise PaymentRequirementError("payment-required must decode to an object")
    return payload


def _validate_resource_url(resource_url: str) -> None:
    """Require a direct HTTPS seller URL without embedded credentials."""

    parsed = urlsplit(resource_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PaymentRequirementError(
            "seller resource URL must be an absolute HTTPS URL without embedded credentials"
        )


def parse_payment_requirement(
    resource_url: str,
    headers: Mapping[str, str],
    body: str,
) -> PaymentRequirement:
    """Parse the first accepted x402 requirement from a 402 response."""

    encoded = next(
        (
            value
            for key, value in headers.items()
            if key.lower() in {"payment-required", "x-payment-required"}
        ),
        "",
    )
    if not encoded:
        raise PaymentRequirementError("seller returned 402 without payment-required")

    payload = _decode_json(encoded)
    accepts = payload.get("accepts")
    if not isinstance(accepts, list) or not accepts or not isinstance(accepts[0], dict):
        raise PaymentRequirementError("payment-required must contain a non-empty accepts list")

    accepted = accepts[0]
    pay_to = str(accepted.get("payTo") or "")
    network = str(accepted.get("network") or "")
    asset = str(accepted.get("asset") or "")
    try:
        amount = int(accepted.get("amount"))
    except (TypeError, ValueError) as error:
        raise PaymentRequirementError("accepted amount must be an integer") from error

    if not pay_to or not network or not asset or amount <= 0:
        raise PaymentRequirementError("accepted requirement is missing required fields")

    return PaymentRequirement(
        resource_url=resource_url,
        pay_to=pay_to,
        amount=amount,
        network=network,
        asset=asset,
        payment_required_request={
            "statusCode": 402,
            "headers": dict(headers),
            "body": body,
        },
    )


def fetch_payment_requirement(
    resource_url: str,
    opener: Callable[..., Any] = _DEFAULT_OPENER,
) -> PaymentRequirement:
    """Fetch a resource and return its requirement when the seller returns 402."""

    _validate_resource_url(resource_url)
    request = Request(resource_url, headers={"Accept": "application/json"})
    try:
        with opener(request, timeout=20) as response:
            raise PaymentRequirementError(
                f"seller returned {response.status}; expected HTTP 402"
            )
    except HTTPError as error:
        if error.code != 402:
            raise
        return parse_payment_requirement(
            resource_url=resource_url,
            headers=dict(error.headers.items()),
            body=error.read().decode("utf-8", errors="replace"),
        )


class PolicyEnforcedBuyer:
    """Purchase client that cannot retry until Policy authorizes the requirement."""

    def __init__(
        self,
        policy_authorizer: PolicyAuthorizer,
        payment_executor: PaymentExecutor,
        opener: Callable[..., Any] = _DEFAULT_OPENER,
    ) -> None:
        self._policy_authorizer = policy_authorizer
        self._payment_executor = payment_executor
        self._opener = opener

    def purchase(self, resource_url: str) -> PurchaseResult:
        """Authorize one seller requirement, then create headers and retry once."""

        requirement = fetch_payment_requirement(resource_url, opener=self._opener)
        self._policy_authorizer.authorize(requirement)
        headers = {"Accept": "application/json", **self._payment_executor.payment_headers(requirement)}
        request = Request(resource_url, headers=headers)

        with self._opener(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed_body = json.loads(body)
            except json.JSONDecodeError:
                parsed_body = {"body": body}
            return PurchaseResult(
                status_code=response.status,
                body=parsed_body,
                requirement=requirement,
                payment_execution=self._payment_executor.execution_label,
                settlement="not-verified",
            )


class AllowlistPolicyAuthorizer:
    """Local policy simulator used only by the no-side-effect E2E test."""

    def __init__(
        self,
        pay_to: str,
        network: str,
        asset: str,
        maximum_amount: int,
    ) -> None:
        self._pay_to = pay_to
        self._network = network
        self._asset = asset
        self._maximum_amount = maximum_amount

    def authorize(self, requirement: PaymentRequirement) -> None:
        mismatches = []
        if requirement.pay_to != self._pay_to:
            mismatches.append("recipient")
        if requirement.network != self._network:
            mismatches.append("network")
        if requirement.asset != self._asset:
            mismatches.append("asset")
        if requirement.amount > self._maximum_amount:
            mismatches.append("amount")
        if mismatches:
            raise PolicyDenied(f"POLICY_DENY: {', '.join(mismatches)}")


class SimulatedPaymentExecutor:
    """Local-only executor that does not create a payment proof or settlement."""

    execution_label = "simulated"

    def payment_headers(self, requirement: PaymentRequirement) -> Mapping[str, str]:
        del requirement
        return {"X-Payment": "simulated-proof"}
