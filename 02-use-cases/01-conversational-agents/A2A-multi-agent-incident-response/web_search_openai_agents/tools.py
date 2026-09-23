from agents import function_tool
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from tavily import TavilyClient
from bedrock_agentcore.memory import MemoryClient
from memory_tool import create_memory_tools

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

MCP_REGION = os.getenv("MCP_REGION")
if not MCP_REGION:
    raise RuntimeError("Missing MCP_REGION environment variable")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
if not TAVILY_API_KEY:
    raise RuntimeError("Missing TAVILY_API_KEY environment variable")

client = MemoryClient(region_name=MCP_REGION)


@function_tool
async def web_search_impl(query: str, top_k: int = 5, recency_days: int | None = None):
    """
    Uses Tavily's search API to return top web results with snippets.
    """
    if not TAVILY_API_KEY:
        raise RuntimeError("Missing TAVILY_API_KEY env var")

    client = TavilyClient(api_key=TAVILY_API_KEY)
    search_kwargs = {
        "query": query,
        "max_results": max(1, min(top_k, 10)),
        "include_domains": None,
        "exclude_domains": None,
    }
    if recency_days:
        # Tavily expects: 'day', 'week', 'month', 'year' (or 'd', 'w', 'm', 'y')
        if recency_days <= 1:
            search_kwargs["time_range"] = "day"
        elif recency_days <= 7:
            search_kwargs["time_range"] = "week"
        elif recency_days <= 30:
            search_kwargs["time_range"] = "month"
        else:
            search_kwargs["time_range"] = "year"

    res = client.search(**search_kwargs)
    results = []
    for item in res.get("results", []):
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "snippet": item.get("content") or item.get("snippet"),
                "score": item.get("score"),
            }
        )
    return {"results": results, "provider": "tavily", "query": query}


@function_tool
def list_local_tools() -> list:
    """List available local tools"""
    return [
        {
            "name": "web_search_impl",
            "description": "Search the web using Tavily API",
            "parameters": {
                "query": "Search query string",
                "top_k": "Number of results to return (max 10)",
                "recency_days": "Filter results by recency in days",
            },
        }
    ]


def _get_memory_tools(memory_id, actor_id, session_id):
    """Get memory tools using the initialized memory_id"""
    if memory_id:
        logger.info(f"Starting agent with memory, using memory_id: {memory_id}")

        return create_memory_tools(
            memory_id,
            client,
            actor_id=actor_id,
            session_id=session_id,
        )
    logger.info("Starting agent without memory (memory_id not set)")
    return []
