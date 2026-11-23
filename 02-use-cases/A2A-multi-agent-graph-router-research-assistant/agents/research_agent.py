"""Research sub-agent with web search and key-point extraction tools."""

from __future__ import annotations

import json
from typing import Annotated

from strands import Agent, tool


@tool
def web_search(
    query: Annotated[str, "The search query to execute"],
    max_results: Annotated[int, "Maximum number of results to return"] = 5,
) -> str:
    """Search the web for information on a topic.

    Returns structured search results with titles, snippets, and URLs.

    Note:
        This is a functional stub. Replace the body with a real search API
        (e.g., Tavily, Brave Search, SerpAPI) for production use.
    """
    # Stub: return realistic placeholder results
    results = [
        {
            "title": f"Research result {i + 1} for: {query}",
            "snippet": f"Key finding #{i + 1} related to '{query}'. "
            "This contains relevant data points and analysis that would come from a real search API.",
            "url": f"https://example.com/research/{i + 1}",
        }
        for i in range(min(max_results, 5))
    ]
    return json.dumps({"query": query, "results": results, "total_results": len(results)})


@tool
def extract_key_points(
    text: Annotated[str, "The text to extract key points from"],  # noqa: ARG001
    focus: Annotated[str, "The focus area for extraction"] = "general",
) -> str:
    """Extract and organize key points from research text.

    Returns structured key points categorized by relevance and confidence.

    Note:
        This is a functional stub. In production, this could use NLP
        pipelines or a secondary LLM call for extraction.
    """
    # Stub: return structured extraction based on input
    extraction = {
        "focus": focus,
        "key_points": [
            {"point": f"Primary finding related to {focus}", "confidence": "high", "source": "extracted"},
            {"point": f"Supporting evidence for {focus}", "confidence": "medium", "source": "extracted"},
            {"point": f"Additional context about {focus}", "confidence": "medium", "source": "inferred"},
        ],
        "summary": f"Analysis of text with focus on '{focus}' yielded 3 key points.",
    }
    return json.dumps(extraction)


def get_tools() -> list:
    """Return the list of research tools."""
    return [web_search, extract_key_points]


SYSTEM_PROMPT = """\
You are a research specialist agent. Your job is to gather information on the given topic
using the available search and extraction tools.

IMPORTANT: Structure your final response with these sections:

FINDINGS:
- List each major finding as a bullet point

KEY_DATA:
- List specific data points, statistics, or facts discovered

GAPS:
- List any information gaps or areas that need further investigation

Always use the web_search tool first to gather information, then use extract_key_points
to organize what you found. Be thorough but concise.
"""


def create_research_agent() -> Agent:
    """Create and return a configured research agent.

    Returns:
        A Strands Agent configured with research tools and system prompt.
    """
    return Agent(
        model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        tools=get_tools(),
        system_prompt=SYSTEM_PROMPT,
        name="research-agent",
        description="Researches topics using web search and key-point extraction",
    )
