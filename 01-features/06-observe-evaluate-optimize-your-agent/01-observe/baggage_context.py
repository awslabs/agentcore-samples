"""
OTel Baggage Context Propagation for AgentCore Observability.

Demonstrates two patterns for enriching every span with contextual metadata:

  1. BaggageSpanProcessor — automatically copies all OTel baggage entries to
     span attributes at export time. Set baggage once at session start and
     every child span (tool calls, model invocations, custom spans) will carry
     the same contextual attributes (e.g. tenant.id, session.id) without any
     per-span instrumentation.

  2. OTEL observability platform toggle — when DISABLE_ADOT_OBSERVABILITY is
     set to "true", the agent switches from CloudWatch (via ADOT) to a third-
     party OTLP endpoint. This lets you route traces to Langfuse, Braintrust,
     Datadog, etc. by changing a single environment variable rather than
     modifying agent code. The toggle is handled by initialising StrandsTelemetry
     with setup_otlp_exporter() when the env var is present.

Baggage values set in this demo:
  - session.id   : unique per invocation, correlates all spans in a session
  - tenant.id    : identifies the customer tenant for multi-tenant observability
  - environment  : DEV / PRD — useful for filtering in dashboards

Usage:
    # CloudWatch (default — via ADOT auto-instrumentation)
    opentelemetry-instrument python baggage_context.py --session-id "demo-001"

    # Third-party OTLP endpoint (e.g. Langfuse)
    DISABLE_ADOT_OBSERVABILITY=true \\
    OTEL_EXPORTER_OTLP_ENDPOINT=https://us.cloud.langfuse.com/api/public/otel \\
    OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64-encoded-key>" \\
        python baggage_context.py --session-id "demo-001"

Prerequisites:
    - OTEL environment variables set (see .env.example)
    - CloudWatch Transaction Search enabled (for CloudWatch mode)
    - AWS credentials configured
    pip install opentelemetry-processor-baggage
"""

import argparse
import logging
import os

from opentelemetry import baggage, context, trace
from opentelemetry.trace import get_tracer_provider
from opentelemetry.processor.baggage import BaggageSpanProcessor, ALLOW_ALL_BAGGAGE_KEYS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Register BaggageSpanProcessor at startup ─────────────────────────────────

tracer_provider = get_tracer_provider()

# Copies all current baggage entries to every span's attributes at export time.
# This means tenant.id, session.id, and environment are visible in every span
# in the CloudWatch trace detail view — no per-span set_attribute() needed.
tracer_provider.add_span_processor(BaggageSpanProcessor(ALLOW_ALL_BAGGAGE_KEYS))

# ── Observability platform toggle ─────────────────────────────────────────────
# When DISABLE_ADOT_OBSERVABILITY=true, the ADOT auto-instrumentation is
# bypassed and Strands sets up its own OTLP exporter.  Configure the endpoint
# via OTEL_EXPORTER_OTLP_ENDPOINT and OTEL_EXPORTER_OTLP_HEADERS env vars.
if os.getenv("DISABLE_ADOT_OBSERVABILITY"):
    from strands.telemetry import StrandsTelemetry

    strands_telemetry = StrandsTelemetry()
    strands_telemetry.setup_otlp_exporter()
    logger.info("ADOT disabled — using Strands OTLP exporter (3P platform mode)")
else:
    logger.info("ADOT active — traces will be exported to CloudWatch")


# ── Baggage Setup ─────────────────────────────────────────────────────────────


def set_session_baggage(session_id: str, tenant_id: str, environment: str = "DEV"):
    """Attach per-session context to OTel baggage.

    BaggageSpanProcessor will automatically propagate these values to every
    span created in the current context — including Strands model calls,
    tool executions, and any custom spans.
    """
    ctx = baggage.set_baggage("session.id", session_id)
    ctx = baggage.set_baggage("tenant.id", tenant_id, context=ctx)
    ctx = baggage.set_baggage("environment", environment, context=ctx)
    token = context.attach(ctx)
    logger.info(
        "Baggage attached: session=%s, tenant=%s, env=%s",
        session_id,
        tenant_id,
        environment,
    )
    return token


# ── Travel Agent ──────────────────────────────────────────────────────────────

from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from ddgs import DDGS  # noqa: E402


@tool
def web_search(query: str) -> str:
    """Search the web for current travel information."""
    tracer = trace.get_tracer("travel_agent.tools", "1.0.0")
    with tracer.start_as_current_span("web_search") as span:
        span.set_attribute("tool.name", "web_search")
        span.set_attribute("search.query", query)
        # Note: tenant.id and session.id are automatically added to this span
        # by BaggageSpanProcessor — no explicit set_attribute() needed here.
        try:
            results = DDGS().text(query, max_results=5)
            formatted = [
                f"{i}. {r.get('title', '')}\n   {r.get('body', '')}\n   {r.get('href', '')}"
                for i, r in enumerate(results, 1)
            ]
            result_text = "\n".join(formatted) if formatted else "No results found."
            span.set_attribute("search.results_count", len(results))
            span.set_status(trace.Status(trace.StatusCode.OK))
            return result_text
        except Exception as e:
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            return f"Search error: {e}"


@tool
def get_weather(location: str) -> str:
    """Get weather conditions for a travel destination."""
    tracer = trace.get_tracer("travel_agent.tools", "1.0.0")
    with tracer.start_as_current_span("get_weather") as span:
        span.set_attribute("tool.name", "get_weather")
        span.set_attribute("weather.location", location)
        weather = f"Sunny, 22°C (72°F) in {location}. Great travel weather!"
        span.set_attribute("weather.result", weather)
        span.set_status(trace.Status(trace.StatusCode.OK))
        return weather


def run_agent(session_id: str):
    model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    model = BedrockModel(model_id=model_id, region_name=region, temperature=0.0, max_tokens=1024)
    agent = Agent(
        model=model,
        system_prompt=(
            "You are an experienced travel agent. Use web_search for destination research "
            "and get_weather to check conditions. Provide concise recommendations."
        ),
        tools=[web_search, get_weather],
        trace_attributes={
            "session.id": session_id,
            "tags": ["Strands", "BaggageContext", "Observability"],
        },
    )

    query = "Plan a 5-day trip to Tokyo for cherry blossom season. What should I know about the weather?"
    logger.info("Running travel agent query...")
    result = agent(query)

    print("\nAgent Response:")
    print("-" * 60)
    print(result)
    print("\nBaggage context results:")
    print("  All spans carry: session.id, tenant.id, environment")
    print("  View in: CloudWatch > GenAI Observability > Traces")
    print("  Filter by tenant.id to isolate per-tenant traces in multi-tenant deployments.")


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Baggage Context Propagation Demo")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    parser.add_argument(
        "--tenant-id",
        default="demo_tenant",
        help="Tenant identifier propagated to all spans",
    )
    parser.add_argument(
        "--environment",
        default="DEV",
        choices=["DEV", "PRD"],
        help="Deployment environment",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    token = set_session_baggage(args.session_id, args.tenant_id, args.environment)
    try:
        run_agent(args.session_id)
    finally:
        context.detach(token)
        logger.info("Context for session '%s' detached", args.session_id)


if __name__ == "__main__":
    main()
