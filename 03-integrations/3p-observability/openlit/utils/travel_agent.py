"""
Travel agent for AgentCore Runtime with OpenLIT observability.

Uses StrandsTelemetry to configure an OTLP exporter pointed at a self-hosted
OpenLIT instance. DISABLE_ADOT_OBSERVABILITY=true bypasses CloudWatch.

Required env vars (set via deploy.py → create_agent_runtime environmentVariables):
    OTEL_EXPORTER_OTLP_ENDPOINT — OpenLIT OTEL endpoint (e.g., http://your-host:4318)
    DISABLE_ADOT_OBSERVABILITY  — must be "true"

Optional:
    OTEL_SERVICE_NAME   — Service name visible in OpenLIT (default: agentcore-travel-agent)

OpenLIT deployment options:
  - Docker: git clone https://github.com/openlit/openlit && cd openlit && docker compose up -d
  - Kubernetes: helm install openlit openlit/openlit
  For AgentCore to reach OpenLIT, it must be accessible at a network-reachable endpoint.
"""

import logging
import os

logging.basicConfig(level=logging.ERROR, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper())

otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
if otel_endpoint:
    logger.info("OpenLIT OTLP configured (endpoint: %s)", otel_endpoint)
else:
    logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT not set — traces will not be sent to OpenLIT")

# ── Agent ──────────────────────────────────────────────────────────────────────

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from ddgs import DDGS  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands.telemetry import StrandsTelemetry  # noqa: E402

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
    # StrandsTelemetry reads OTEL_EXPORTER_OTLP_ENDPOINT from environment
    StrandsTelemetry().setup_otlp_exporter()

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
