"""AgentCore HTTP entrypoint for the distributed supervisor.

The supervisor runs the router locally and delegates to remote A2A
sub-agents on separate AgentCore runtimes. Uses the same GraphBuilder
topology as the local demo (demo/run_demo.py).

Environment variables (set during deployment):
    RESEARCH_AGENT_URL: AgentCore runtime URL for the research agent
    ANALYSIS_AGENT_URL: AgentCore runtime URL for the analysis agent
    WRITER_AGENT_URL: AgentCore runtime URL for the writer agent
    AWS_REGION: AWS region for SigV4 signing (default: us-west-2)
"""

from __future__ import annotations

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

_graph = None


def _get_graph():
    """Return the supervisor graph with remote A2A agents, creating it on first call."""
    global _graph  # noqa: PLW0603
    if _graph is None:
        from deployment.sigv4_auth import SigV4A2AAgent
        from graph import build_supervisor_graph

        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))

        research_url = os.environ["RESEARCH_AGENT_URL"]
        analysis_url = os.environ["ANALYSIS_AGENT_URL"]
        writer_url = os.environ["WRITER_AGENT_URL"]

        _graph = build_supervisor_graph(
            research_agent=SigV4A2AAgent(endpoint=research_url, region=region),
            analysis_agent=SigV4A2AAgent(endpoint=analysis_url, region=region),
            writer_agent=SigV4A2AAgent(endpoint=writer_url, region=region),
        )
    return _graph


@app.entrypoint
async def invoke(payload: dict, context: dict) -> dict:  # noqa: ARG001
    """Handle an invocation request.

    Args:
        payload: Request payload containing a "prompt" key.
        context: AgentCore runtime context (unused).

    Returns:
        Dict with the final report and execution metadata.
    """
    prompt = payload.get("prompt", "")
    if not prompt:
        return {"status": "error", "message": "Missing 'prompt' in request payload"}

    graph = _get_graph()
    result = await graph.invoke_async(prompt)

    synthesizer_result = result.results.get("synthesizer")
    report = str(synthesizer_result.result) if synthesizer_result else ""

    return {
        "report": report,
        "execution_summary": {
            "status": result.status.value,
            "execution_order": [n.node_id for n in result.execution_order],
            "total_nodes": result.total_nodes,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "execution_time_ms": result.execution_time,
        },
    }


if __name__ == "__main__":
    app.run()
