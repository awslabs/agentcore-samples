"""
Travel agent for AgentCore Runtime with Arize observability.

Telemetry is sent to Arize via the OpenInference OTel bridge using gRPC OTLP.
DISABLE_ADOT_OBSERVABILITY=true bypasses CloudWatch so this custom TracerProvider
is used instead.

Required env vars (set via deploy.py → create_agent_runtime environmentVariables):
    ARIZE_API_KEY       — Arize API key
    ARIZE_SPACE_ID      — Arize space ID
    ARIZE_ENDPOINT      — Arize OTLP endpoint (default: https://otlp.arize.com:443)
    ARIZE_PROJECT_NAME  — Project name visible in Arize (default: agentcore-travel-agent)
    DISABLE_ADOT_OBSERVABILITY — must be "true"
"""

import logging
import os

logging.basicConfig(level=logging.ERROR, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper())

# ── Arize OTel Setup ───────────────────────────────────────────────────────────
# Must be before any other OTel imports — DISABLE_ADOT_OBSERVABILITY bypasses
# the default CloudWatch ADOT pipeline so we can set our own TracerProvider.

arize_api_key = os.environ.get("ARIZE_API_KEY", "")
arize_space_id = os.environ.get("ARIZE_SPACE_ID", "")
arize_endpoint = os.environ.get("ARIZE_ENDPOINT", "https://otlp.arize.com:443")
arize_project = os.environ.get("ARIZE_PROJECT_NAME", "agentcore-travel-agent")

if arize_api_key and arize_space_id:
    from openinference.instrumentation.strands_agents import (
        StrandsAgentsToOpenInferenceProcessor,
    )
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"model_id": arize_project})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(StrandsAgentsToOpenInferenceProcessor())
    exporter = OTLPSpanExporter(
        endpoint=arize_endpoint,
        headers=f"space_id={arize_space_id},api_key={arize_api_key}",
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("Arize OTel configured (project: %s)", arize_project)
else:
    logger.warning("ARIZE_API_KEY or ARIZE_SPACE_ID not set — traces will not be sent to Arize")

# ── Agent ──────────────────────────────────────────────────────────────────────

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from ddgs import DDGS  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402

app = BedrockAgentCoreApp()


@tool
def web_search(query: str) -> str:
    """Search the web for current travel information."""
    try:
        ddgs = DDGS()
        results = ddgs.text(query, max_results=5)
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"{i}. {r.get('title', 'No title')}\n"
                f"   {r.get('body', 'No summary')}\n"
                f"   Source: {r.get('href', 'No URL')}\n"
            )
        return "\n".join(formatted) if formatted else "No results found."
    except Exception as e:
        return f"Search error: {str(e)}"


def create_agent():
    model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    model = BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=1024)
    return Agent(
        model=model,
        system_prompt=(
            "You are an experienced travel agent. Use web_search for destination research "
            "and provide concise, well-sourced recommendations."
        ),
        tools=[web_search],
    )


@app.entrypoint
def invoke(payload, context=None):
    user_input = payload.get("prompt", "")
    logger.info("[%s] %s", getattr(context, "session_id", "local"), user_input)
    agent = create_agent()
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
