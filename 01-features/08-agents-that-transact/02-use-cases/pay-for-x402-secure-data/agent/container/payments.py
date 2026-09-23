"""Payment context and AgentCore payments plugin configuration helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


def resolve_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"


@dataclass(frozen=True)
class PaymentContext:
    """Per-invocation payment identifiers required to call ProcessPayment."""

    user_id: str
    payment_session_id: str
    payment_instrument_id: str

    @classmethod
    def from_env(cls) -> PaymentContext:
        return cls(
            user_id=os.environ.get("USER_ID", "x402-secure-data-user"),
            payment_session_id=os.environ["PAYMENT_SESSION_ID"],
            payment_instrument_id=os.environ["PAYMENT_INSTRUMENT_ID"],
        )


@dataclass(frozen=True)
class PaymentPluginConfigValues:
    """Resolved configuration passed to AgentCorePaymentsPlugin."""

    payment_manager_arn: str
    user_id: str
    payment_session_id: str
    payment_instrument_id: str
    region: str
    agent_name: str
    payment_connector_id: str | None = None


_CURRENT_PAYMENT_CONTEXT: ContextVar[PaymentContext | None] = ContextVar(
    "x402_secure_data_current_payment_context",
    default=None,
)
_REQUIRE_REQUEST_PAYMENT_CONTEXT: ContextVar[bool] = ContextVar(
    "x402_secure_data_require_request_payment_context",
    default=False,
)


@contextmanager
def use_invocation_payment_context(
    payment_context: PaymentContext | None,
    *,
    require_payment_context: bool = False,
):
    """Bind the payment context for the duration of one invocation.

    Args:
        payment_context: Context to expose to payment-config resolution, or None.
        require_payment_context: When True, downstream resolution raises if context is missing.
    """
    payment_token = _CURRENT_PAYMENT_CONTEXT.set(payment_context)
    requirement_token = _REQUIRE_REQUEST_PAYMENT_CONTEXT.set(require_payment_context)
    try:
        yield
    finally:
        _CURRENT_PAYMENT_CONTEXT.reset(payment_token)
        _REQUIRE_REQUEST_PAYMENT_CONTEXT.reset(requirement_token)


def resolve_payment_plugin_config_values() -> PaymentPluginConfigValues | None:
    """Resolve plugin config from the active invocation context or environment.

    Returns:
        Config values, or None when no payment context/env is available and none is required.

    Raises:
        ValueError: If payment context is required for the request but missing.
    """
    payment_context = _CURRENT_PAYMENT_CONTEXT.get()
    if payment_context is None:
        if _REQUIRE_REQUEST_PAYMENT_CONTEXT.get():
            raise ValueError(
                "Paid x402 tools via /invocations require payment_context with "
                "user_id, payment_session_id, and payment_instrument_id."
            )
        required_env = ["MANAGER_ARN", "PAYMENT_SESSION_ID", "PAYMENT_INSTRUMENT_ID"]
        if not all(os.environ.get(name) for name in required_env):
            return None
        payment_context = PaymentContext.from_env()

    manager_arn = os.environ.get("MANAGER_ARN")
    if not manager_arn:
        if _REQUIRE_REQUEST_PAYMENT_CONTEXT.get():
            raise ValueError("Paid x402 tools require MANAGER_ARN.")
        return None

    region = resolve_region()
    return PaymentPluginConfigValues(
        payment_manager_arn=manager_arn,
        user_id=payment_context.user_id,
        payment_session_id=payment_context.payment_session_id,
        payment_instrument_id=payment_context.payment_instrument_id,
        region=region,
        agent_name=os.environ.get("AGENT_NAME", "pay-for-x402-secure-data"),
        payment_connector_id=os.environ.get("PAYMENT_CONNECTOR_ID") or None,
    )
