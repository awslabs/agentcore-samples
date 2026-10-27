"""Agent invocation — streaming wrapper around invoke_harness."""

import os
import sys
from collections.abc import Generator
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
from utils.client import get_agentcore_client

# Resolved here rather than imported from resources.py: resources.py imports
# SYSTEM_PROMPT from this module, so importing REGION back from it would be a
# cycle and neither module would load. Same order as utils/client.py.
REGION = (
    os.environ.get("AWS_DEFAULT_REGION")
    or os.environ.get("AWS_REGION")
    or boto3.session.Session().region_name
    or "us-east-1"
)

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# The single definition of the agent's instructions. resources.py sets this on
# the harness at create time and optimization.py reports it as the current
# prompt, so both import it from here rather than keeping their own copy — two
# copies had already drifted apart, and the one the optimizer offered to improve
# was not the one the harness was actually running.
SYSTEM_PROMPT = (
    "You are a weather assistant. You ONLY answer questions about weather, "
    "climate, and atmospheric conditions (temperature, wind, humidity, UV index, "
    "sunrise, sunset, moon phase, forecasts, air quality, precipitation). "
    "If the user asks about anything unrelated to weather, politely redirect them. "
    "For example: 'I'm a weather assistant — I can help with forecasts, current conditions, "
    "UV index, wind, sunrise/sunset, and more. What location would you like weather for?' "
    "When answering weather questions: always search for real-time data using your tools, "
    "include specific numbers with units (temperature in F/C, wind in km/h or mph), "
    "mention the city name in your response, and keep responses concise and well-structured."
)


def _screen(text: str, guardrail_id: str, guardrail_version: str) -> str:
    """Run text through the guardrail, returning the (possibly redacted) text.

    CreateHarness/InvokeHarness take no guardrail parameter, so a guardrail is
    not something the harness applies on its own — the response has to be passed
    through ApplyGuardrail explicitly. Returns the text unchanged on any failure:
    a broken guardrail must not swallow the answer.
    """
    if not (guardrail_id and guardrail_version and text):
        return text
    try:
        resp = boto3.client("bedrock-runtime", region_name=REGION).apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion=guardrail_version,
            source="OUTPUT",
            content=[{"text": {"text": text}}],
        )
        if resp.get("action") == "GUARDRAIL_INTERVENED" and resp.get("outputs"):
            return resp["outputs"][0].get("text", text)
    except Exception as e:  # noqa: BLE001 - screening must not break the stream
        print(f"[agent] Guardrail screening failed: {type(e).__name__}: {e}")
    return text


def invoke_agent(
    harness_arn: str,
    gateway_arn: str,
    session_id: str,
    message: str,
    guardrail_id: str | None = None,
    guardrail_version: str | None = None,
) -> Generator[dict, None, None]:
    """Stream agent response as SSE-friendly dicts.

    Any failure is reported as an `error` event rather than raised: the caller is
    already streaming an HTTP response by the time this generator runs, so an
    exception here just truncates the stream and the browser shows nothing at all.

    When a guardrail is passed, the finished answer is screened and a `redacted`
    event carries the screened text. The web app created a guardrail, billed for
    it and displayed its id in the header, but never applied it to anything — so
    the PII-anonymization pillar the app claims to demonstrate did nothing at all.
    Screening has to happen on the complete answer rather than per delta, because
    an entity can straddle two deltas and a partial match would slip through.
    """
    client = get_agentcore_client()

    tools = [
        {
            "type": "agentcore_gateway",
            "name": "gateway",
            "config": {"agentCoreGateway": {"gatewayArn": gateway_arn}},
        }
    ]

    full_text = ""
    try:
        # No system prompt here: resources.py already sets it on the harness at
        # create time. Prefixing it onto the user's message sent it twice, and it
        # became part of the stored conversation history — so every later turn in
        # the session paid for it again and the model saw the instructions
        # repeated as something the user had said.
        response = client.invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            messages=[{"role": "user", "content": [{"text": message}]}],
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
                    full_text += delta["text"]
                    yield {"type": "text", "content": delta["text"]}
            elif "internalServerException" in event:
                yield {"type": "error", "content": str(event["internalServerException"])}
    except Exception as e:  # noqa: BLE001 - screening must not break the stream
        yield {"type": "error", "content": f"{type(e).__name__}: {e}"}

    if guardrail_id:
        screened = _screen(full_text, guardrail_id, guardrail_version)
        if screened != full_text:
            yield {"type": "redacted", "content": screened}

    # A single terminal event. `messageStop` fires once per message in the agent
    # loop, so emitting `done` there sent one `done` per tool-use round-trip and
    # the client saw the response "finish" before the answer had even started.
    yield {"type": "done"}
