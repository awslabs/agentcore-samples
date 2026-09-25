"""Runtime invocation validation independent of the AgentCore SDK."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from buyer.core import PolicyDenied


@dataclass(frozen=True)
class RuntimePaymentContext:
    """Payment context created by the application backend for one invocation."""

    manager_arn: str
    user_id: str
    session_id: str
    instrument_id: str
    gateway_url: str
    target_name: str
    seller_base_url: str
    policy_session_id: str


def runtime_context(payload: dict[str, Any]) -> RuntimePaymentContext:
    """Validate the application-owned values required for a Runtime invocation."""

    return RuntimePaymentContext(
        manager_arn=_required(payload, "payment_manager_arn"),
        user_id=_required(payload, "user_id"),
        session_id=_required(payload, "payment_session_id"),
        instrument_id=_required(payload, "payment_instrument_id"),
        gateway_url=_required(payload, "policy_gateway_url"),
        target_name=str(payload.get("policy_target_name") or "PaymentPolicyTools"),
        seller_base_url=_required(payload, "seller_base_url").rstrip("/"),
        policy_session_id=str(payload.get("policy_session_id") or uuid.uuid4()),
    )


def validate_seller_url(resource_url: str, seller_base_url: str) -> None:
    """Reject purchase URLs that are outside the configured seller origin."""

    resource = urlparse(resource_url)
    expected = urlparse(seller_base_url)
    if (
        expected.scheme != "https"
        or not expected.netloc
        or expected.username is not None
        or expected.password is not None
    ):
        raise ValueError(
            "seller_base_url must be an absolute HTTPS URL without embedded credentials"
        )
    if (resource.scheme, resource.netloc) != (expected.scheme, expected.netloc):
        raise PolicyDenied("POLICY_DENY: resource URL is outside the approved seller origin")


def _required(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Missing required invocation field: {key}")
    return value
