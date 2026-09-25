"""
MCP Server with Advanced Features — Tools, Resources, and Prompts.

Demonstrates the full range of MCP server capabilities supported
by AgentCore Runtime.
"""

import json
from datetime import datetime, timezone
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# host, port, path and stateless_http are the AgentCore Runtime service contract: the
# runtime fixes the MCP port at 8000, needs 0.0.0.0 for its health check, and gives no
# session affinity, so the server must keep no transport state between requests.
#
# json_response=True is this sample's own choice, NOT a runtime requirement: it makes
# every reply a plain JSON body so invoke.py can json.loads() it. See the README for the
# trade-off — in JSON mode progress notifications and server-initiated requests
# (sampling, elicitation) are dropped, which is why this sample does not demonstrate them.
mcp = FastMCP(
    "advanced-tools",
    host="0.0.0.0",  # nosec B104
    port=8000,
    stateless_http=True,
    json_response=True,
)


# ── Tools ────────────────────────────────────────────────────────────────────


# Keep tool docstrings to a single line. FastMCP publishes the whole docstring as the
# tool's `description`, so Args:/Returns: blocks are sent to every client on every
# tools/list — duplicating what the generated inputSchema already says.
#
# max_results is annotated with its real bound rather than a bare int, so the published
# schema states the limit instead of silently truncating a larger request.
@mcp.tool()
def search_documents(query: str, max_results: Annotated[int, Field(ge=1, le=5)] = 5) -> str:
    """Search a document database and return matches as JSON."""
    # Mock search results
    results = [
        {
            "id": f"doc-{i}",
            "title": f"Document about {query} (#{i})",
            "score": 0.95 - i * 0.1,
        }
        for i in range(max_results)
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
def analyze_sentiment(text: str) -> str:
    """Analyze the sentiment of a text and return the result as JSON."""
    # Mock sentiment analysis
    word_count = len(text.split())
    return json.dumps(
        {
            "sentiment": "positive" if word_count > 5 else "neutral",
            "confidence": 0.87,
            "word_count": word_count,
        }
    )


@mcp.tool()
def get_timestamp() -> str:
    """Get the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ── Resources ────────────────────────────────────────────────────────────────


# mime_type matters: both these resources return JSON, and without it FastMCP
# advertises them as text/plain in resources/list and resources/read.
@mcp.resource("config://app", mime_type="application/json")
def get_app_config() -> str:
    """Application configuration settings."""
    return json.dumps(
        {
            "version": "2.1.0",
            "environment": "production",
            "features": {
                "search": True,
                "sentiment_analysis": True,
                "caching": False,
            },
        },
        indent=2,
    )


@mcp.resource("data://system-status", mime_type="application/json")
def get_system_status() -> str:
    """Current system status and health metrics."""
    return json.dumps(
        {
            "status": "healthy",
            "uptime_hours": 142.5,
            "active_connections": 23,
            "last_check": datetime.now(timezone.utc).isoformat(),
        },
        indent=2,
    )


# ── Prompts ──────────────────────────────────────────────────────────────────


@mcp.prompt()
def code_review(code: str, language: str = "python") -> str:
    """Generate a code review prompt for the given source code."""
    return (
        f"Please review the following {language} code for:\n"
        f"1. Correctness and potential bugs\n"
        f"2. Performance considerations\n"
        f"3. Security vulnerabilities\n"
        f"4. Code style and readability\n\n"
        f"```{language}\n{code}\n```"
    )


@mcp.prompt()
def summarize_document(document: str, max_length: str = "200 words") -> str:
    """Generate a summarization prompt for the given document."""
    return (
        f"Summarize the following document in {max_length} or less. "
        f"Focus on key points and actionable insights.\n\n"
        f"Document:\n{document}"
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
