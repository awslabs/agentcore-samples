"""
Travel agent for AgentCore Runtime with Dash0 observability.

Configures OTel TracerProvider, MeterProvider, and LoggerProvider exporting
traces, metrics, and logs to Dash0's OTLP HTTP endpoint via Bearer token auth.
DISABLE_ADOT_OBSERVABILITY=true bypasses CloudWatch so Dash0 receives the telemetry.

Required env vars (set via deploy.py → create_agent_runtime environmentVariables):
    DASH0_AUTH_TOKEN        — Auth token from Settings → Auth Tokens
    DASH0_OTLP_ENDPOINT     — OTLP ingress base URL (default: https://ingress.us-west-2.aws.dash0.com)
    DASH0_DATASET           — Dataset name (default: default)
    OTEL_SERVICE_NAME       — Service name visible in Dash0
    DISABLE_ADOT_OBSERVABILITY — must be "true"
"""

import logging
import os

logging.basicConfig(level=logging.ERROR, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("AGENT_RUNTIME_LOG_LEVEL", "INFO").upper())

# ── Dash0 OTel Setup ───────────────────────────────────────────────────────────
# Must be configured BEFORE any other OTel imports.

auth_token = os.environ.get("DASH0_AUTH_TOKEN", "")
otlp_base = os.environ.get("DASH0_OTLP_ENDPOINT", "https://ingress.us-west-2.aws.dash0.com").rstrip("/")
dataset = os.environ.get("DASH0_DATASET", "default")
service_name = os.environ.get("OTEL_SERVICE_NAME", "agentcore-travel-agent")

if auth_token:
    from opentelemetry import metrics, trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk._logs import LoggingHandler
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    headers = {"Authorization": f"Bearer {auth_token}", "Dash0-Dataset": dataset}
    resource = Resource.create({"service.name": service_name})

    # Traces
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(
        SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_base}/v1/traces", headers=headers))
    )
    trace.set_tracer_provider(trace_provider)

    # Metrics
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{otlp_base}/v1/metrics", headers=headers))
            ],
        )
    )

    # Logs
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{otlp_base}/v1/logs", headers=headers))
    )
    set_logger_provider(log_provider)
    logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=log_provider))

    logger.info(
        "Dash0 OTel configured (service: %s, dataset: %s, endpoint: %s)",
        service_name,
        dataset,
        otlp_base,
    )
else:
    logger.warning("DASH0_AUTH_TOKEN not set — telemetry will not be sent to Dash0")

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
