"""
AgentCore Observability — CrewAI Travel Agent (non-runtime hosted).

Demonstrates how to instrument a CrewAI agent running outside AgentCore Runtime
so its traces appear in the CloudWatch GenAI Observability dashboard.

CrewAI's built-in telemetry is disabled to avoid conflicts with ADOT.
The CrewAIInstrumentor bridges CrewAI spans into the OTel pipeline.

Prerequisites:
    - CloudWatch Transaction Search enabled (see 05-infrastructure-as-code/)
    - OTEL environment variables set (see .env.example)
    - CloudWatch log group created (see setup.py)

Usage:
    opentelemetry-instrument python crewai_travel_agent.py
"""

import os
import logging

# Disable CrewAI's built-in telemetry to avoid conflicts with ADOT
os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"

from dotenv import load_dotenv
from crewai import Agent, Crew, LLM, Task
from crewai.tools import tool
from ddgs import DDGS
from opentelemetry.instrumentation.crewai import CrewAIInstrumentor

load_dotenv()

# Instrument CrewAI with OpenTelemetry
CrewAIInstrumentor().instrument()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@tool("web_search")
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

    llm = LLM(
        model=f"bedrock/{model_id}",
        temperature=0.0,
        max_tokens=512,
        aws_region_name=region,
    )

    travel_agent = Agent(
        role="Travel Destination Researcher",
        goal="Find dream destinations matching user preferences using web search for current information",
        backstory=(
            "You are an experienced travel agent specializing in personalized travel "
            "recommendations with access to real-time web information."
        ),
        verbose=True,
        allow_delegation=False,
        llm=llm,
        max_iter=3,
        tools=[web_search],
    )

    task = Task(
        description=(
            "Research and recommend suitable travel destinations for someone looking for "
            "cowboy vibes, rodeos, and museums. Use web search to find current information "
            "about venues, events, and attractions."
        ),
        expected_output=(
            "A comprehensive list of recommended destinations with current information, "
            "brief descriptions, and practical travel details."
        ),
        agent=travel_agent,
    )

    crew = Crew(agents=[travel_agent], tasks=[task], verbose=True)
    result = crew.kickoff()
    print("\nResult:", result)


if __name__ == "__main__":
    main()
