"""A2A server for the writer agent.

Exposes the writer/synthesizer agent as a standalone A2A-compatible service.

Usage:
    python -m deployment.serve_writer

Serves on http://0.0.0.0:9003 by default.
"""

from __future__ import annotations

from strands.multiagent.a2a import A2AServer

from agents import create_writer_agent


def main() -> None:
    """Start the writer agent A2A server."""
    agent = create_writer_agent()
    server = A2AServer(agent, host="0.0.0.0", port=9003)  # noqa: S104
    print("Starting writer agent A2A server on http://0.0.0.0:9003")
    server.serve()


if __name__ == "__main__":
    main()
