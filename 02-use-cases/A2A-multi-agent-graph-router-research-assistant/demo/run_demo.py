"""Local CLI demo for the router-supervisor agent.

Runs the full graph locally without any AgentCore dependency.

Usage:
    python -m demo.run_demo --prompt "Research the impact of AI on healthcare"
    python -m demo.run_demo --prompt "Write a brief summary of cloud computing" --json
"""

from __future__ import annotations

import argparse
import json

from agents import create_analysis_agent, create_research_agent, create_writer_agent
from graph import build_supervisor_graph


def main() -> None:
    """Run the router-supervisor graph with a user prompt."""
    parser = argparse.ArgumentParser(description="Router-Supervisor Agent Demo")
    parser.add_argument("--prompt", required=True, help="The prompt to process")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output results as JSON")
    args = parser.parse_args()

    # Create local agents
    research_agent = create_research_agent()
    analysis_agent = create_analysis_agent()
    writer_agent = create_writer_agent()

    # Build and run the graph
    graph = build_supervisor_graph(research_agent, analysis_agent, writer_agent)
    result = graph(args.prompt)

    if args.json_output:
        output = {
            "status": result.status.value,
            "execution_order": [node.node_id for node in result.execution_order],
            "total_nodes": result.total_nodes,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "results": {},
        }
        for node_id, node_result in result.results.items():
            output["results"][node_id] = {
                "status": node_result.status.value,
                "output": str(node_result.result),
                "execution_time_ms": node_result.execution_time,
            }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 80)
        print("ROUTER-SUPERVISOR AGENT — Execution Complete")
        print("=" * 80)

        print(f"\nStatus: {result.status.value}")
        print(f"Execution order: {' -> '.join(n.node_id for n in result.execution_order)}")
        print(f"Nodes: {result.completed_nodes}/{result.total_nodes} completed")

        # Print router decision
        router_result = result.results.get("router")
        if router_result:
            print(f"\n--- Router Decision ---\n{router_result.result}")

        # Print synthesizer output (the final report)
        synthesizer_result = result.results.get("synthesizer")
        if synthesizer_result:
            print(f"\n{'=' * 80}")
            print("FINAL REPORT")
            print(f"{'=' * 80}\n")
            print(str(synthesizer_result.result))


if __name__ == "__main__":
    main()
