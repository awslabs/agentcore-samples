"""Distributed A2A supervisor entrypoint.

The supervisor graph uses remote A2AAgent clients to communicate with
sub-agents running as separate A2A servers. Same graph topology as the
local demo (demo/run_demo.py), but agents are distributed across processes.

Prerequisites:
    Start each sub-agent server first:
        python -m deployment.serve_research   # port 9001
        python -m deployment.serve_analysis   # port 9002
        python -m deployment.serve_writer     # port 9003

    Set environment variables (or use defaults):
        export RESEARCH_AGENT_URL=http://localhost:9001
        export ANALYSIS_AGENT_URL=http://localhost:9002
        export WRITER_AGENT_URL=http://localhost:9003

Usage:
    python -m deployment.main --prompt "Research the impact of AI on healthcare"
"""

from __future__ import annotations

import argparse
import json
import os

from strands.agent.a2a_agent import A2AAgent

from graph import build_supervisor_graph


def main() -> None:
    """Run the distributed supervisor graph."""
    parser = argparse.ArgumentParser(description="Distributed Router-Supervisor Agent")
    parser.add_argument("--prompt", required=True, help="The prompt to process")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output results as JSON")
    args = parser.parse_args()

    # Create remote agent clients
    research_agent = A2AAgent(endpoint=os.environ.get("RESEARCH_AGENT_URL", "http://localhost:9001"))
    analysis_agent = A2AAgent(endpoint=os.environ.get("ANALYSIS_AGENT_URL", "http://localhost:9002"))
    writer_agent = A2AAgent(endpoint=os.environ.get("WRITER_AGENT_URL", "http://localhost:9003"))

    # Build graph with remote agents — same topology as local deployment
    graph = build_supervisor_graph(research_agent, analysis_agent, writer_agent)
    result = graph(args.prompt)

    if args.json_output:
        output = {
            "status": result.status.value,
            "execution_order": [node.node_id for node in result.execution_order],
            "results": {},
        }
        for node_id, node_result in result.results.items():
            output["results"][node_id] = {
                "status": node_result.status.value,
                "output": str(node_result.result),
            }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 80)
        print("DISTRIBUTED ROUTER-SUPERVISOR — Execution Complete")
        print("=" * 80)
        print(f"\nStatus: {result.status.value}")
        print(f"Execution order: {' -> '.join(n.node_id for n in result.execution_order)}")

        synthesizer_result = result.results.get("synthesizer")
        if synthesizer_result:
            print(f"\n{'=' * 80}")
            print("FINAL REPORT")
            print(f"{'=' * 80}\n")
            print(str(synthesizer_result.result))


if __name__ == "__main__":
    main()
