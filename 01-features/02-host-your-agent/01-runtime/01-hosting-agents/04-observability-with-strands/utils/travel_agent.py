"""
Travel Agent for AgentCore Runtime with built-in observability.

This agent is instrumented with AWS OpenTelemetry (ADOT) automatically when
deployed to AgentCore Runtime. The runtime start command uses:
    opentelemetry-instrument python travel_agent.py

The ADOT instrumentation captures all Strands spans, Bedrock LLM calls, and
tool invocations and sends them to CloudWatch GenAI Observability.
"""

import os
import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel
from ddgs import DDGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# ── Tools ──────────────────────────────────────────────────────────────────────


@tool
def web_search(query: str) -> str:
    """Search the web for current information about travel destinations and events."""
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


@tool
def get_weather(location: str) -> str:
    """Get current weather conditions for a travel destination."""
    # Placeholder — replace with a real weather API in production
    return f"Weather in {location}: Sunny, 22°C (72°F). Great time to travel!"


# ── Agent ──────────────────────────────────────────────────────────────────────


def create_agent() -> Agent:
    model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

    model = BedrockModel(
        model_id=model_id,
        region_name=region,
        temperature=0.0,
        max_tokens=4096,
    )

    return Agent(
        model=model,
        system_prompt=(
            "You are an experienced travel agent specializing in personalized recommendations. "
            "Use web search to find current information about destinations, attractions, and events. "
            "Use get_weather to check conditions before recommending travel. "
            "Provide comprehensive, well-sourced recommendations."
        ),
        tools=[web_search, get_weather],
        trace_attributes={
            "service.name": "strands-travel-agent",
            "tags": ["Strands", "AgentCore", "Observability"],
        },
    )


# ── AgentCore Runtime Entrypoint ───────────────────────────────────────────────


@app.entrypoint
def invoke_travel_agent(payload):
    """Process a travel query via AgentCore Runtime."""
    user_input = payload.get("prompt", "")
    logger.info("Processing travel query: %s", user_input[:100])

    agent = create_agent()
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
