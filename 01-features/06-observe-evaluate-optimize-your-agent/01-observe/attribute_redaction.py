"""
Attribute Redaction for AgentCore Observability.

Demonstrates two complementary OTel span processor patterns for protecting
sensitive data before spans are exported to CloudWatch:

  1. BaggageSpanProcessor — copies all OTel baggage entries (e.g. tenant.id,
     user.email) to span attributes so they are available for trace correlation.
     Registered first so downstream processors can act on propagated attributes.

  2. SensitiveDataRedactor — a custom SpanProcessor that scans on_end and
     replaces a configurable list of sensitive attributes with "[REDACTED]".
     Applied after BaggageSpanProcessor so it can catch attributes that were
     propagated via baggage.

The combined effect:
  - tenant.id: propagated via baggage → visible in all child spans (kept)
  - user.email: propagated via baggage → immediately redacted (dropped)
  - llm.prompts, gen_ai.input.messages, llm.completions: redacted

The travel agent below attaches tenant.id and user.email as baggage, then
runs a query. Inspect the resulting CloudWatch trace to verify that user.email
never appears in the exported spans.

Usage:
    # Run with ADOT instrumentation (required for CloudWatch export)
    opentelemetry-instrument python attribute_redaction.py --session-id "demo-001"

Prerequisites:
    - OTEL environment variables set (see .env.example)
    - CloudWatch Transaction Search enabled
    - AWS credentials configured
    pip install opentelemetry-processor-baggage
"""

import argparse
import logging
import os
from typing import ClassVar

from opentelemetry import baggage, context, trace
from opentelemetry.processor.baggage import ALLOW_ALL_BAGGAGE_KEYS, BaggageSpanProcessor
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import get_tracer_provider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── SensitiveDataRedactor ─────────────────────────────────────────────────────


class SensitiveDataRedactor(SpanProcessor):
    """Redact sensitive attributes from spans before they are exported.

    The redaction happens in on_end by overwriting the span's internal
    _attributes dict. This ensures the data never leaves the process
    in clear text.

    Attributes covered:
      - llm.prompts             : raw LLM prompt text
      - gen_ai.input.messages   : full message list sent to the model
      - llm.completions         : raw LLM completion text
      - user.email              : PII propagated via OTel baggage
    """

    SENSITIVE_ATTRS: ClassVar[list[str]] = [
        "llm.prompts",
        "gen_ai.input.messages",
        "llm.completions",
        "user.email",
    ]

    def on_end(self, span: ReadableSpan):
        if not span.attributes:
            return
        present = [attr for attr in self.SENSITIVE_ATTRS if attr in span.attributes]
        if not present:
            return
        # span.attributes is a BoundedAttributes instance that is immutable once
        # the span has ended (item assignment raises TypeError). Rebuild a plain
        # dict with the sensitive values masked and reassign the private slot.
        # Reassigning the attribute avoids depending on the internal storage name,
        # which differs across OTel SDK versions (_dict vs _attributes).
        redacted = dict(span.attributes)
        for attr in present:
            redacted[attr] = "[REDACTED]"
            logger.debug("Redacted attribute '%s' in span '%s'", attr, span.name)
        span._attributes = redacted  # pylint: disable=protected-access


# ── Register processors at startup ───────────────────────────────────────────

tracer_provider = get_tracer_provider()

# 1. Propagate all baggage entries to span attributes (tenant.id, user.email, …)
tracer_provider.add_span_processor(BaggageSpanProcessor(ALLOW_ALL_BAGGAGE_KEYS))

# 2. Immediately redact sensitive attributes (including those just propagated)
tracer_provider.add_span_processor(SensitiveDataRedactor())


# ── Baggage Setup ─────────────────────────────────────────────────────────────


def set_user_context(session_id: str, tenant_id: str, user_email: str):
    """Attach tenant and user context to OTel baggage.

    Both values are propagated to all child spans by BaggageSpanProcessor.
    user_email is then stripped by SensitiveDataRedactor before export,
    while tenant_id is preserved for trace correlation.
    """
    ctx = baggage.set_baggage("session.id", session_id)
    ctx = baggage.set_baggage("tenant.id", tenant_id, context=ctx)
    ctx = baggage.set_baggage("user.email", user_email, context=ctx)
    token = context.attach(ctx)
    logger.info("Baggage context attached: tenant=%s, email=<redacted>", tenant_id)
    return token


# ── Travel Agent ──────────────────────────────────────────────────────────────

from ddgs import DDGS
from strands import Agent, tool
from strands.models import BedrockModel


@tool
def web_search(query: str) -> str:
    """Search the web for current travel information."""
    tracer = trace.get_tracer("travel_agent.tools", "1.0.0")
    with tracer.start_as_current_span("web_search") as span:
        span.set_attribute("tool.name", "web_search")
        span.set_attribute("search.query", query)
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
        except Exception as e:  # noqa: BLE001 — demo tool surfaces any search failure as text
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
            "tags": ["Strands", "AttributeRedaction", "Observability"],
        },
    )

    query = "What are the top travel destinations in Italy for summer 2025?"
    logger.info("Running travel agent query...")
    result = agent(query)

    print("\nAgent Response:")
    print("-" * 60)
    print(result)
    print("\nAttribute redaction results:")
    print("  tenant.id   → visible in CloudWatch (kept for trace correlation)")
    print("  user.email  → [REDACTED] — never exported to CloudWatch")
    print("  llm.prompts → [REDACTED] if present in Strands OTel attributes")


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Attribute Redaction Demo")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    parser.add_argument("--tenant-id", default="demo_tenant", help="Tenant ID (kept in spans)")
    parser.add_argument(
        "--user-email",
        default="demo@anycompany.com",
        help="User email (redacted from spans)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    token = set_user_context(args.session_id, args.tenant_id, args.user_email)
    try:
        run_agent(args.session_id)
    finally:
        context.detach(token)
        logger.info("Context for session '%s' detached", args.session_id)


if __name__ == "__main__":
    main()
