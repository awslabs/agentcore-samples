"""AgentCore Runtime entrypoint for the Tutorial 01 OpenAI payment agent."""

from __future__ import annotations

import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from openai_payment_agent import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REGION,
    build_agent,
    build_model,
    payment_region,
    run_agent,
)
from openai_x402_tool import build_x402_fetch

app = BedrockAgentCoreApp()


def _unwrap_payload(payload: dict) -> dict:
    """Handle the JSON-string wrapper used by some AgentCore CLI versions."""
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return payload
    try:
        structured_prompt = json.loads(prompt)
    except json.JSONDecodeError:
        return payload
    return structured_prompt if isinstance(structured_prompt, dict) else payload


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Run the OpenAI agent with a caller-created, budget-bounded session."""
    payload = _unwrap_payload(payload)
    required_fields = (
        "prompt",
        "payment_manager_arn",
        "payment_session_id",
        "payment_instrument_id",
        "user_id",
    )
    missing_fields = [name for name in required_fields if not payload.get(name)]
    if missing_fields:
        return {"error": f"Missing required fields in payload: {', '.join(missing_fields)}"}

    payment_manager_arn = payload["payment_manager_arn"]
    x402_fetch = build_x402_fetch(
        payment_manager_arn=payment_manager_arn,
        payment_instrument_id=payload["payment_instrument_id"],
        payment_session_id=payload["payment_session_id"],
        user_id=payload["user_id"],
        region=payment_region(payment_manager_arn),
    )
    agent = build_agent(
        build_model(
            os.getenv("BEDROCK_OPENAI_MODEL_REGION", DEFAULT_MODEL_REGION),
            os.getenv("BEDROCK_OPENAI_MODEL_ID", DEFAULT_MODEL_ID),
        ),
        x402_fetch,
    )
    return {"result": run_agent(agent, payload["prompt"])}


if __name__ == "__main__":
    app.run()
