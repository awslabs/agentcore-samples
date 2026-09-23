"""
Span Filtering for AgentCore Observability.

Demonstrates two complementary approaches to selectively include or exclude
OpenTelemetry spans before they are exported to CloudWatch:

  1. FilterSpanProcessor — filters after the span is recorded (on_end).
     Use this when you need access to span attributes, duration, or status
     to make the filtering decision.

  2. FilterSpanSampler — filters at span creation time (should_sample).
     More efficient: filtered spans are never recorded in memory.
     Use this for name-based or route-based filtering where you know
     upfront which spans to drop.

  3. OTEL_PYTHON_EXCLUDED_URLS — the simplest approach: exclude all spans
     for URL patterns matching a glob (e.g. "*/invocations" drops the
     AgentCore health-check invocations).

The travel agent below runs with both a FilterSpanProcessor (drops short-lived
spans < 50 ms) and logs a note about using OTEL_PYTHON_EXCLUDED_URLS.

Usage:
    # Run with ADOT instrumentation (required for CloudWatch export)
    opentelemetry-instrument python span_filters.py --session-id "demo-001"

    # To also filter by URL pattern at the collector level:
    OTEL_PYTHON_EXCLUDED_URLS="*/invocations" \\
        opentelemetry-instrument python span_filters.py --session-id "demo-001"

Prerequisites:
    - OTEL environment variables set (see .env.example)
    - CloudWatch Transaction Search enabled
    - AWS credentials configured
"""

import argparse
import logging
import os
import re

from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.sampling import Sampler, SamplingResult, Decision
from opentelemetry.trace import get_tracer_provider
from opentelemetry.context import Context
from opentelemetry.trace.span import TraceState
from opentelemetry.util.types import Attributes
from typing import Optional, Sequence

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── FilterSpanProcessor ───────────────────────────────────────────────────────


class FilterSpanProcessor(SpanProcessor):
    """Span processor that drops spans not passing a filter function.

    The filter_func receives a ReadableSpan and returns True to keep the span
    or False to drop it before export.
    """

    def __init__(self, filter_func):
        self.filter_func = filter_func

    def on_end(self, span: ReadableSpan):
        if self.filter_func(span):
            super().on_end(span)
        else:
            logger.debug("Filtered out span: %s", span.name)


def filter_by_duration(span: ReadableSpan) -> bool:
    """Keep only spans that took more than 50 ms.

    Very short spans (e.g. internal bookkeeping) rarely add diagnostic value
    and can be dropped to reduce CloudWatch ingestion costs.
    """
    if isinstance(span, ReadableSpan) and span.end_time and span.start_time:
        duration_ms = (span.end_time - span.start_time) / 1_000_000
        return duration_ms > 50
    return True


def filter_by_name_prefix(span: ReadableSpan) -> bool:
    """Drop spans whose names start with 'POST' (e.g. HTTP POST health checks)."""
    return not span.name.lower().startswith("post")


def filter_by_route(span: ReadableSpan) -> bool:
    """Drop spans from the /health endpoint."""
    if hasattr(span, "attributes") and span.attributes:
        if span.attributes.get("http.route") == "/health":
            return False
    return True


def filter_by_pattern(span: ReadableSpan) -> bool:
    """Drop spans whose names match the 'internal.*' pattern."""
    return not re.match(r"^internal\..*", span.name)


# ── FilterSpanSampler ─────────────────────────────────────────────────────────


def _get_parent_trace_state(parent_context: Optional[Context]) -> Optional[TraceState]:
    parent_span_ctx = trace.get_current_span(parent_context).get_span_context()
    if parent_span_ctx is None or not parent_span_ctx.is_valid:
        return None
    return parent_span_ctx.trace_state


class FilterSpanSampler(Sampler):
    """Sampler that drops spans at creation time based on a name filter function.

    Because the decision is made before the span is created, filtered spans
    never consume memory — more efficient than FilterSpanProcessor for
    high-cardinality workloads.
    """

    def __init__(self, filter_func):
        self.filter_func = filter_func

    def should_sample(
        self,
        parent_context: Optional[Context],
        trace_id: int,
        name: str,
        kind=None,
        attributes: Attributes = None,
        links: Optional[Sequence] = None,
        trace_state: Optional[TraceState] = None,
    ) -> SamplingResult:
        if self.filter_func(name):
            return SamplingResult(
                decision=Decision.RECORD_AND_SAMPLE,
                attributes=attributes,
                trace_state=_get_parent_trace_state(parent_context),
            )
        return SamplingResult(decision=Decision.DROP)

    def get_description(self) -> str:
        return "FilterSpanSampler"


def drop_post_spans(span_name: str) -> bool:
    """Return False (drop) for spans whose names start with POST."""
    return not span_name.lower().startswith("post")


# ── Register processors at startup ───────────────────────────────────────────

tracer_provider = get_tracer_provider()

# Register a processor that drops very short spans
tracer_provider.add_span_processor(FilterSpanProcessor(filter_by_duration))

# Tip: to filter by URL pattern without code changes, set the env var:
#   OTEL_PYTHON_EXCLUDED_URLS="*/invocations"
# This tells the ADOT auto-instrumentation to skip spans for matching URLs.
if os.getenv("OTEL_PYTHON_EXCLUDED_URLS"):
    logger.info(
        "OTEL_PYTHON_EXCLUDED_URLS=%s — URL-based span exclusion active",
        os.getenv("OTEL_PYTHON_EXCLUDED_URLS"),
    )


# ── Travel Agent with Span Filtering ─────────────────────────────────────────

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
            "tags": ["Strands", "SpanFiltering", "Observability"],
        },
    )

    query = "What are the best places to visit in Kyoto, Japan in spring, and what will the weather be like?"
    logger.info("Running travel agent query...")
    result = agent(query)

    print("\nAgent Response:")
    print("-" * 60)
    print(result)
    print("\nFiltered spans visible in:")
    print("  CloudWatch > GenAI Observability > Bedrock AgentCore > Traces")
    print("\nSpans < 50 ms were dropped by FilterSpanProcessor.")
    print("Set OTEL_PYTHON_EXCLUDED_URLS='*/invocations' to also drop URL-matched spans.")


# ── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Span Filtering Demo")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    return parser.parse_args()


def main():
    args = parse_args()
    ctx = baggage.set_baggage("session.id", args.session_id)
    token = context.attach(ctx)
    try:
        run_agent(args.session_id)
    finally:
        context.detach(token)


if __name__ == "__main__":
    main()
