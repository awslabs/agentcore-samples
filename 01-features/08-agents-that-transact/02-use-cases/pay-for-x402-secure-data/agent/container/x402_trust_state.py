from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

TrustState = dict[str, tuple[float, dict[str, Any]]]
_CURRENT_TRUST_STATE: ContextVar[TrustState | None] = ContextVar(
    "x402_secure_data_current_trust_state",
    default=None,
)


@contextmanager
def use_request_trust_state() -> Iterator[None]:
    token = _CURRENT_TRUST_STATE.set({})
    try:
        yield
    finally:
        _CURRENT_TRUST_STATE.reset(token)


def require_request_trust_state() -> TrustState:
    state = _CURRENT_TRUST_STATE.get()
    if state is None:
        raise KeyError("No active request-scoped x402-secure trust state")
    return state


def cached_request_trust_result(endpoint_url: str, now: float) -> dict[str, Any]:
    cached = require_request_trust_state().get(endpoint_url)
    if cached and cached[0] > now:
        return cached[1]
    raise KeyError(f"No valid x402-secure trust result for {endpoint_url}")


def store_request_trust_result(endpoint_url: str, expires_at: float, result: dict[str, Any]) -> None:
    require_request_trust_state()[endpoint_url] = (expires_at, result)
