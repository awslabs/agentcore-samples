"""Agent invocation — streaming wrapper around invoke_harness."""

import sys
from pathlib import Path
from typing import Generator

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from utils.client import get_agentcore_client

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = (
    "You are a weather assistant. You ONLY answer questions about weather, "
    "climate, and atmospheric conditions (temperature, wind, humidity, UV index, "
    "sunrise, sunset, moon phase, forecasts, air quality, precipitation). "
    "If the user asks about anything unrelated to weather, politely redirect them. "
    "For example: 'I'm a weather assistant — I can help with forecasts, current conditions, "
    "UV index, wind, sunrise/sunset, and more. What location would you like weather for?'\n\n"
    "When answering weather questions:\n"
    "- Always search for real-time data using your tools\n"
    "- Include specific numbers with units (temperature in °F/°C, wind in km/h or mph)\n"
    "- Mention the city name in your response\n"
    "- Keep responses concise and well-structured"
)


def invoke_agent(harness_arn: str, gateway_arn: str, session_id: str, message: str) -> Generator[dict, None, None]:
    """Stream agent response as SSE-friendly dicts."""
    client = get_agentcore_client()

    tools = [
        {
            "type": "agentcore_gateway",
            "name": "gateway",
            "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
        }
    ]

    prefixed_message = f"[INSTRUCTIONS: {SYSTEM_PROMPT}]\n\nUser question: {message}"

    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": prefixed_message}]}],
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        tools=tools,
    )

    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                yield {"type": "tool", "name": start["toolUse"].get("name", "?")}
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                yield {"type": "text", "content": delta["text"]}
        elif "messageStop" in event:
            yield {"type": "done"}
        elif "internalServerException" in event:
            yield {"type": "error", "content": str(event["internalServerException"])}
