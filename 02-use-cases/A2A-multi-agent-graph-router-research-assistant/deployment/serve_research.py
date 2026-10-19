"""A2A server for the research agent.

Exposes the research agent as a standalone A2A-compatible service.

Usage:
    python -m deployment.serve_research

Serves on http://0.0.0.0:9001 by default.
"""

from __future__ import annotations

from strands.multiagent.a2a import A2AServer

from agents import create_research_agent


def main() -> None:
    """Start the research agent A2A server."""
    agent = create_research_agent()
    server = A2AServer(agent, host="0.0.0.0", port=9001)  # noqa: S104
    print("Starting research agent A2A server on http://0.0.0.0:9001")
    server.serve()


if __name__ == "__main__":
    main()
