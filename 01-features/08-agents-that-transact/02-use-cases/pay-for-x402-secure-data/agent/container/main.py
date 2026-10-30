from __future__ import annotations

from typing import Any

from agent import create_agent, resolve_model_id
from http_app import create_app
from http_app import invoke_payload as _invoke_payload
from payments import PaymentContext
from runtime_context import (
    configure_logging,
    extract_metrics_attributes,
    extract_payment_context,
    extract_session_id,
    mask_identifier,
)

__all__ = [
    "app",
    "configure_logging",
    "create_agent",
    "extract_metrics_attributes",
    "extract_payment_context",
    "extract_session_id",
    "invoke_payload",
    "mask_identifier",
    "resolve_model_id",
]

app = create_app(agent_factory=lambda: create_agent(), model_resolver=resolve_model_id)


def invoke_payload(
    payload: dict[str, Any],
    agent_instance: Any | None = None,
    session_id: str | None = None,
    payment_context: PaymentContext | None = None,
    require_payment_context: bool = False,
) -> dict[str, Any]:
    return _invoke_payload(
        payload,
        agent_factory=lambda: create_agent(),
        model_resolver=resolve_model_id,
        agent_instance=agent_instance,
        session_id=session_id,
        payment_context=payment_context,
        require_payment_context=require_payment_context,
    )


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    # AgentCore Runtime routes traffic into the container on all interfaces,
    # so bind to 0.0.0.0 inside the container by default. Override with
    # HOST=127.0.0.1 when running the container directly on a developer
    # machine. The container has no inbound network path other than the
    # Runtime's, so binding all interfaces is intentional and required here.
    host = os.environ.get("HOST", "0.0.0.0")  # nosec B104 — required by AgentCore Runtime
    uvicorn.run(app, host=host, port=port)
