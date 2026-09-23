"""
AgentCore Observability — Strands Travel Agent with Session Tracking.

Extends strands_travel_agent.py by attaching a session ID to OpenTelemetry baggage
so that all spans from this run are grouped under one session in CloudWatch GenAI
Observability.

Usage:
    opentelemetry-instrument python strands_travel_agent_with_session.py --session-id "user-session-123"
"""

import argparse
import logging
import os

from dotenv import load_dotenv
from opentelemetry import baggage, context
from strands import Agent, tool
from strands.models import BedrockModel
from ddgs import DDGS

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("strands").setLevel(logging.INFO)


def parse_args():
    parser = argparse.ArgumentParser(description="Strands Travel Agent with Session Tracking")
    parser.add_argument("--session-id", required=True, help="Session ID for trace correlation")
    return parser.parse_args()


def set_session_context(session_id: str):
    """Attach session ID to OTel baggage so all spans share the session context."""
    ctx = baggage.set_baggage("session.id", session_id)
    token = context.attach(ctx)
    logger.info("Session '%s' attached to telemetry context", session_id)
    return token


@tool
def web_search(query: str) -> str:
    """Search the web for current information about travel destinations, attractions, and events."""
    try:
        ddgs = DDGS()
        results = ddgs.text(query, max_results=5)
        formatted = []
        for i, result in enumerate(results, 1):
            formatted.append(
                f"{i}. {result.get('title', 'No title')}\n"
                f"   {result.get('body', 'No summary')}\n"
                f"   Source: {result.get('href', 'No URL')}\n"
            )
        return "\n".join(formatted) if formatted else "No results found."
    except Exception as e:
        return f"Search error: {str(e)}"


def run_agent(session_id: str):
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
            "You are an experienced travel agent specializing in personalized recommendations "
            "with access to real-time web information. Use web_search for current destination "
            "info and provide concise, well-sourced recommendations."
        ),
        tools=[web_search],
        trace_attributes={
            "session.id": session_id,
            "tags": ["Strands", "Observability"],
        },
    )

    query = (
        "Research and recommend suitable travel destinations for someone looking for cowboy "
        "vibes, rodeos, and museums. Use web search to find current information about venues, "
        "events, and attractions."
    )
    result = agent(query)
    print("\nAgent Response:")
    print("-" * 60)
    print(result)


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
