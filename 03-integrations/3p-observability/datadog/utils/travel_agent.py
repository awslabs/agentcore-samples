"""
Travel agent for AgentCore Runtime with Datadog LLM Observability.

Configures a custom OTel TracerProvider that exports to Datadog's OTLP HTTP
endpoint. DISABLE_ADOT_OBSERVABILITY=true bypasses CloudWatch so Datadog
receives the traces.

Required env vars (set via deploy.py → create_agent_runtime environmentVariables):
    DD_API_KEY          — Datadog API key
    DD_SITE             — Datadog site (default: datadoghq.com)
    OTEL_SERVICE_NAME   — ML app name visible in Datadog LLM Observability
    DISABLE_ADOT_OBSERVABILITY — must be "true"
"""

import logging
import os

logging.basicConfig(level=logging.ERROR, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper())

# ── Datadog OTel Setup ─────────────────────────────────────────────────────────
# Must be configured BEFORE any other OTel imports.
# OTEL_SEMCONV_STABILITY_OPT_IN enables OTel v1.37+ GenAI semantic conventions
# required by Strands Agents for Datadog LLM Observability.

os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")

dd_api_key = os.environ.get("DD_API_KEY", "")
dd_site = os.environ.get("DD_SITE", "datadoghq.com")
service_name = os.environ.get("OTEL_SERVICE_NAME", "agentcore-travel-agent")

if dd_api_key:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    resource = Resource.create({"service.name": service_name})
    exporter = OTLPSpanExporter(
        endpoint=f"https://trace.agent.{dd_site}/v1/traces",
        headers={"dd-api-key": dd_api_key, "dd-otlp-source": "llmobs"},
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info("Datadog OTel configured (service: %s, site: %s)", service_name, dd_site)
else:
    logger.warning("DD_API_KEY not set — traces will not be sent to Datadog")

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
