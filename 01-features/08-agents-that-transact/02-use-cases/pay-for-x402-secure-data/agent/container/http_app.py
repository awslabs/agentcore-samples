from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from opentelemetry import baggage, trace
from opentelemetry import context as otel_context
from payments import PaymentContext, use_invocation_payment_context
from runtime_context import (
    configure_logging,
    extract_metrics_attributes,
    extract_payment_context,
    extract_session_id,
    mask_identifier,
)
from x402_services import use_request_trust_state

LOGGER = logging.getLogger("x402_secure_data.main")
TRACER = trace.get_tracer("x402_secure_data.main")


def invoke_payload(
    payload: dict[str, Any],
    *,
    agent_factory: Callable[[], Any],
    model_resolver: Callable[[], str],
    agent_instance: Any | None = None,
    session_id: str | None = None,
    payment_context: PaymentContext | None = None,
    require_payment_context: bool = False,
) -> dict[str, Any]:
    prompt = payload.get("prompt") or payload.get("input", {}).get("prompt", "")
    if not prompt:
        raise ValueError("No prompt found. Provide payload.prompt or payload.input.prompt.")
    if require_payment_context and payment_context is None:
        raise ValueError(
            "Paid /invocations calls require payment_context with "
            "user_id, payment_session_id, and payment_instrument_id."
        )

    model_id = model_resolver()
    with TRACER.start_as_current_span("pay-for-x402-secure-data.invoke") as span:
        masked_session_id = mask_identifier(session_id)
        span.set_attribute("x402_secure_data.model_id", model_id)
        span.set_attribute("x402_secure_data.prompt_length", len(prompt))
        if masked_session_id:
            span.set_attribute("session.id", masked_session_id)
            span.set_attribute("x402_secure_data.session_id", masked_session_id)

        with (
            use_invocation_payment_context(
                payment_context,
                require_payment_context=require_payment_context,
            ),
            use_request_trust_state(),
        ):
            resolved_agent = agent_instance or agent_factory()
            result = resolved_agent(prompt)
        message = getattr(result, "message", str(result))
        metric_attributes = extract_metrics_attributes(result)
        for key, value in metric_attributes.items():
            span.set_attribute(key, value)

        LOGGER.info(
            "x402_secure_data.invoke.complete %s",
            json.dumps(
                {
                    "session_id": masked_session_id,
                    "model": model_id,
                    "prompt_length": len(prompt),
                    **metric_attributes,
                }
            ),
        )

        return {
            "output": {
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model": "pay-for-x402-secure-data",
            }
        }


def create_app(agent_factory: Callable[[], Any], model_resolver: Callable[[], str]) -> FastAPI:
    configure_logging()
    app = FastAPI(title="Pay for Secure Data (x402) Agent Server", version="1.0.0")

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        body = await request.body()
        try:
            parsed_payload = json.loads(body.decode() or "{}")
        except json.JSONDecodeError:
            parsed_payload = {}
        if not isinstance(parsed_payload, dict):
            parsed_payload = {}
        session_id = extract_session_id(request.headers, parsed_payload)
        masked_session_id = mask_identifier(session_id)

        LOGGER.info(
            "x402_secure_data.http.request %s",
            json.dumps(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "content_type": request.headers.get("content-type"),
                    "body_len": len(body),
                    "session_id": masked_session_id,
                }
            ),
        )

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = receive
        request.state.parsed_payload = parsed_payload
        request.state.session_id = session_id
        request.state.payment_context_error = None
        request.state.payment_context = None
        try:
            request.state.payment_context = extract_payment_context(request.headers, parsed_payload)
        except ValueError as exc:
            request.state.payment_context_error = str(exc)

        token = None
        current_span = trace.get_current_span()
        if session_id:
            ctx = baggage.set_baggage("session.id", masked_session_id or "***")
            token = otel_context.attach(ctx)
            if current_span.is_recording():
                current_span.set_attribute("session.id", masked_session_id or "***")
                current_span.set_attribute("x402_secure_data.session_id", masked_session_id or "***")

        try:
            response = await call_next(request)
        finally:
            if token is not None:
                otel_context.detach(token)

        LOGGER.info(
            "x402_secure_data.http.response %s",
            json.dumps(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "session_id": masked_session_id,
                }
            ),
        )
        return response

    @app.post("/invocations")
    async def invoke_agent(request: Request):
        try:
            payment_context_error = getattr(request.state, "payment_context_error", None)
            if payment_context_error:
                raise HTTPException(status_code=400, detail=payment_context_error)

            payload = getattr(request.state, "parsed_payload", None)
            if payload is None:
                raw_body = await request.body()
                try:
                    payload = json.loads(raw_body.decode() or "{}")
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
                if not isinstance(payload, dict):
                    raise HTTPException(status_code=400, detail="Request body must be a JSON object.")

            # Strands Agent.__call__ is fully blocking (multiple Bedrock turns +
            # the paid x402 calls, ~30s-2min). Run it in a worker thread so the
            # uvicorn event loop stays free to serve GET /ping — otherwise the
            # Docker/AgentCore health checks time out and the instance can be
            # recycled mid-invocation (after USDC settles, before the response).
            # asyncio.to_thread copies the current context, so the ContextVar-
            # scoped trust/payment state and the OTel span are preserved.
            return await asyncio.to_thread(
                invoke_payload,
                payload,
                agent_factory=agent_factory,
                model_resolver=model_resolver,
                session_id=getattr(request.state, "session_id", None),
                payment_context=getattr(request.state, "payment_context", None),
                require_payment_context=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Agent processing failed.") from exc

    @app.get("/ping")
    async def ping():
        return {"status": "healthy"}

    return app
