"""
Travel agent for AgentCore Runtime with Langfuse observability.

Uses StrandsTelemetry to configure an OTLP exporter pointed at Langfuse.
DISABLE_ADOT_OBSERVABILITY=true bypasses CloudWatch so Langfuse receives the traces.

Required env vars (set via deploy.py → create_agent_runtime environmentVariables):
    LANGFUSE_PUBLIC_KEY — Langfuse public key
    LANGFUSE_SECRET_KEY — Langfuse secret key
    LANGFUSE_HOST       — Langfuse host URL (default: https://us.cloud.langfuse.com)
    DISABLE_ADOT_OBSERVABILITY — must be "true"
"""

import base64
import logging
import os

logging.basicConfig(level=logging.ERROR, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper())

# Pre-configure OTLP env vars from Langfuse credentials so StrandsTelemetry
# can read them via setup_otlp_exporter().
langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
langfuse_host = os.environ.get("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

if langfuse_public_key and langfuse_secret_key:
    auth_token = base64.b64encode(f"{langfuse_public_key}:{langfuse_secret_key}".encode()).decode()
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{langfuse_host}/api/public/otel"
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {auth_token}"
    logger.info("Langfuse OTLP configured (host: %s)", langfuse_host)
else:
    logger.warning("LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY not set — traces will not be sent to Langfuse")

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
    # StrandsTelemetry reads OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_EXPORTER_OTLP_HEADERS set above
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
