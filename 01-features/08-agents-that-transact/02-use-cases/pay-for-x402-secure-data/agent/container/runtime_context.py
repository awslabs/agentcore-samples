from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

from payments import PaymentContext


def configure_logging() -> None:
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    logging.getLogger("strands").setLevel(
        getattr(logging, os.environ.get("STRANDS_LOG_LEVEL", log_level_name).upper(), log_level)
    )


def mask_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"...{value[-6:]}"


def extract_session_id(headers: Mapping[str, str], payload: dict[str, Any]) -> str | None:
    input_payload = payload.get("input", {})
    if not isinstance(input_payload, Mapping):
        input_payload = {}
    return (
        headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id")
        or headers.get("x-amzn-bedrock-agentcore-runtime-session-id")
        or payload.get("session_id")
        or payload.get("sessionId")
        or input_payload.get("session_id")
        or input_payload.get("sessionId")
    )


def extract_payment_context(headers: Mapping[str, str], payload: dict[str, Any]) -> PaymentContext | None:
    input_payload = payload.get("input", {})
    if input_payload and not isinstance(input_payload, Mapping):
        raise ValueError("payload.input must be an object.")
    payload_context = payload.get("payment_context") or input_payload.get("payment_context") or {}
    if payload_context and not isinstance(payload_context, Mapping):
        raise ValueError("payment_context must be an object.")

    payment_session_id = headers.get("X-Payment-Session-Id") or payload_context.get("payment_session_id")
    payment_instrument_id = headers.get("X-Payment-Instrument-Id") or payload_context.get("payment_instrument_id")
    user_id = headers.get("X-User-Id") or headers.get("x-user-id") or payload_context.get("user_id")

    if not any((payment_session_id, payment_instrument_id, user_id)):
        return None

    missing_fields = [
        field_name
        for field_name, value in (
            ("payment_session_id", payment_session_id),
            ("payment_instrument_id", payment_instrument_id),
            ("user_id", user_id),
        )
        if not value
    ]
    if missing_fields:
        raise ValueError(f"Incomplete payment_context. Provide {', '.join(missing_fields)}.")

    return PaymentContext(
        user_id=user_id,
        payment_session_id=payment_session_id,
        payment_instrument_id=payment_instrument_id,
    )


def extract_metrics_attributes(agent_result: Any) -> dict[str, Any]:
    metrics = getattr(agent_result, "metrics", None)
    if metrics is None or not hasattr(metrics, "get_summary"):
        return {}

    summary = metrics.get_summary()
    usage = summary.get("accumulated_usage", {})
    tool_usage = summary.get("tool_usage", {})

    def safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    return {
        "x402_secure_data.total_cycles": safe_int(summary.get("total_cycles", 0)),
        "x402_secure_data.total_duration_s": safe_float(summary.get("total_duration", 0.0)),
        "x402_secure_data.total_tokens": safe_int(usage.get("totalTokens", 0)),
        "x402_secure_data.input_tokens": safe_int(usage.get("inputTokens", 0)),
        "x402_secure_data.output_tokens": safe_int(usage.get("outputTokens", 0)),
        "x402_secure_data.tool_names": ",".join(sorted(tool_usage.keys())),
    }
