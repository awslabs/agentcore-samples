"""A2A server for the analysis agent.

Exposes the analysis agent as a standalone A2A-compatible service.

Usage:
    python -m deployment.serve_analysis

Serves on http://0.0.0.0:9002 by default.
"""

from __future__ import annotations

from strands.multiagent.a2a import A2AServer

from agents import create_analysis_agent


def main() -> None:
    """Start the analysis agent A2A server."""
    agent = create_analysis_agent()
    server = A2AServer(agent, host="0.0.0.0", port=9002)  # noqa: S104
    print("Starting analysis agent A2A server on http://0.0.0.0:9002")
    server.serve()


if __name__ == "__main__":
    main()
