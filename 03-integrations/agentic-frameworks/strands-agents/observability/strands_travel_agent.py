"""
AgentCore Observability — Strands Travel Agent (non-runtime hosted).

Demonstrates how to instrument a Strands agent running outside AgentCore Runtime
(e.g., on EC2, Lambda, or locally) so its traces appear in the CloudWatch GenAI
Observability dashboard.

The AWS OpenTelemetry Distro (ADOT) auto-instruments Strands, Bedrock API calls,
and tool invocations when you run with `opentelemetry-instrument`.

Prerequisites:
    - CloudWatch Transaction Search enabled (see 05-infrastructure-as-code/)
    - OTEL environment variables set (see .env.example)
    - CloudWatch log group created (see setup.py)

Usage:
    # Load .env then run with ADOT instrumentation
    opentelemetry-instrument python strands_travel_agent.py
"""

import os
import logging
from dotenv import load_dotenv
from strands import Agent, tool
from strands.models import BedrockModel
from ddgs import DDGS

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("strands").setLevel(logging.INFO)


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


def main():
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


if __name__ == "__main__":
    main()
