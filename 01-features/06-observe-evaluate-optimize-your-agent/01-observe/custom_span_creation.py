"""
Custom Span Creation for AgentCore Observability.

Demonstrates how to create custom OpenTelemetry spans within a Strands agent
to get deeper visibility into specific operations (tool calls, processing steps)
in the CloudWatch GenAI Observability dashboard.

Custom spans let you:
  - Track specific operations with precise timing
  - Add business-specific attributes for filtering and analysis
  - Record important events during agent execution
  - Capture detailed error context when things go wrong

The agent in this example is a travel agent that uses web search. The web_search
tool wraps its logic in a custom span with rich attributes, giving you granular
visibility into every search operation in CloudWatch Traces.

Usage:
    # Run with ADOT instrumentation (required for CloudWatch export)
    opentelemetry-instrument python custom_span_creation.py --session-id "demo-session-001"

    # Or set OTEL env vars in .env and use dotenv:
    python -m dotenv run -- opentelemetry-instrument python custom_span_creation.py --session-id "demo-001"

Prerequisites:
    - OTEL environment variables set (see .env.example)
    - CloudWatch Transaction Search enabled
    - AWS credentials configured
"""

import argparse
import logging
import os

from opentelemetry import baggage, context, trace

# ── Configuration ──────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Session Setup ──────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Custom Span Creation Demo")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    return parser.parse_args()


def set_session_context(session_id: str):
    """Attach session ID to OTel baggage so all spans share the session context."""
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    logger.info("Session '%s' attached to telemetry context", session_id)
    return token


# ── Agent Tools with Custom Spans ─────────────────────────────────────────────

from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402


@tool
def web_search(query: str) -> str:
    """Search the web for current travel information. Instrumented with a custom span."""
    tracer = trace.get_tracer("travel_agent.tools", "1.0.0")

    with tracer.start_as_current_span("web_search") as span:
        # Add descriptive attributes visible in CloudWatch trace detail view
        span.set_attribute("tool.name", "web_search")
        span.set_attribute("search.query", query)
        span.set_attribute("search.provider", "duckduckgo")

        span.add_event("search_started", {"query": query[:200]})

        try:
            from ddgs import DDGS

            ddgs = DDGS()
            results = ddgs.text(query, max_results=5)

            span.set_attribute("search.results_count", len(results))

            formatted = []
            for i, r in enumerate(results, 1):
                formatted.append(
                    f"{i}. {r.get('title', 'No title')}\n"
                    f"   {r.get('body', 'No summary')}\n"
                    f"   Source: {r.get('href', 'No URL')}\n"
                )
                # Capture top-3 result metadata as span attributes
                if i <= 3:
                    span.set_attribute(f"search.result_{i}.title", r.get("title", "")[:100])
                    span.set_attribute(f"search.result_{i}.url", r.get("href", ""))

            result_text = "\n".join(formatted) if formatted else "No results found."
            span.set_attribute("search.results_preview", result_text[:500])

            span.add_event("search_completed", {"results_count": len(results), "success": True})
            span.set_status(trace.Status(trace.StatusCode.OK))

            logger.info("Web search OK: %d results for '%s'", len(results), query[:50])
            return result_text

        except Exception as e:
            span.set_attribute("error.type", type(e).__name__)
            span.set_attribute("error.message", str(e))
            span.add_event("search_failed", {"error": str(e)})
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            logger.error("Web search failed: %s", e)
            return f"Search error: {str(e)}"


@tool
def get_weather(location: str) -> str:
    """Get weather conditions for a travel destination."""
    tracer = trace.get_tracer("travel_agent.tools", "1.0.0")

    with tracer.start_as_current_span("get_weather") as span:
        span.set_attribute("tool.name", "get_weather")
        span.set_attribute("weather.location", location)
        # Placeholder — replace with a real API call
        weather = f"Sunny, 22°C (72°F) in {location}. Perfect travel weather!"
        span.set_attribute("weather.result", weather)
        span.set_status(trace.Status(trace.StatusCode.OK))
        return weather


# ── Agent with Session-level Custom Span ──────────────────────────────────────


def run_agent(session_id: str):
    tracer = trace.get_tracer("travel_agent", "1.0.0")

    with tracer.start_as_current_span("travel_agent_session") as session_span:
        session_span.set_attribute("session.id", session_id)
        session_span.set_attribute("agent.type", "travel_agent")
        session_span.set_attribute("agent.framework", "strands")

        model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

        model = BedrockModel(
            model_id=model_id,
            region_name=region,
            temperature=0.0,
            max_tokens=1024,
        )

        agent = Agent(
            model=model,
            system_prompt=(
                "You are an experienced travel agent. Use web_search for destination research "
                "and get_weather to check conditions. Provide concise, well-sourced recommendations."
            ),
            tools=[web_search, get_weather],
            trace_attributes={
                "session.id": session_id,
                "tags": ["Strands", "CustomSpans", "Observability"],
            },
        )

        query = "What are the best places to visit in Kyoto, Japan in spring, and what will the weather be like?"
        session_span.add_event("query_started", {"query": query[:200]})

        result = agent(query)

        session_span.add_event("query_completed", {"success": True})
        session_span.set_status(trace.Status(trace.StatusCode.OK))

        print("\nAgent Response:")
        print("-" * 60)
        print(result)
        print("\nCustom spans visible in:")
        print("  CloudWatch > GenAI Observability > Bedrock AgentCore > Traces")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    ctx_token = set_session_context(args.session_id)

    try:
        run_agent(args.session_id)
    finally:
        context.detach(ctx_token)
        logger.info("Session context for '%s' detached", args.session_id)


if __name__ == "__main__":
    main()
