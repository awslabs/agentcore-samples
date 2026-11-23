"""Supervisor graph assembly using Strands GraphBuilder.

Wires the router, worker sub-agents, and synthesizer into a conditional
graph with AND-join semantics for the synthesizer node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from strands.multiagent.graph import Graph, GraphBuilder

from .conditions import (
    analysis_to_synthesizer,
    research_to_synthesizer,
    router_direct_to_synthesizer,
    should_dispatch_analysis,
    should_dispatch_research,
)
from .router_agent import create_router_agent

if TYPE_CHECKING:
    from strands.agent.agent import AgentBase


def build_supervisor_graph(
    research_agent: AgentBase,
    analysis_agent: AgentBase,
    writer_agent: AgentBase,
    *,
    execution_timeout: float = 600.0,
    node_timeout: float = 300.0,
) -> Graph:
    """Build the router-supervisor graph.

    The graph topology is deployment-agnostic: agents can be local ``Agent``
    instances or remote ``A2AAgent`` clients — both extend ``AgentBase``.

    Graph structure::

        router ──(conditional)──> research ──(AND-join)──> synthesizer
               ──(conditional)──> analysis ──(AND-join)──┘
               ──(direct)─────────────────────────────────┘

    Args:
        research_agent: Agent (or A2AAgent) that performs research tasks.
        analysis_agent: Agent (or A2AAgent) that performs analysis tasks.
        writer_agent: Agent (or A2AAgent) that synthesizes the final report.
        execution_timeout: Max total graph execution time in seconds.
        node_timeout: Max execution time per node in seconds.

    Returns:
        A compiled Graph ready to be invoked with ``graph(prompt)``.
    """
    builder = GraphBuilder()

    # Add nodes
    builder.add_node(create_router_agent(), "router")
    builder.add_node(research_agent, "research")
    builder.add_node(analysis_agent, "analysis")
    builder.add_node(writer_agent, "synthesizer")

    # Router -> workers (conditional dispatch)
    builder.add_edge("router", "research", condition=should_dispatch_research)
    builder.add_edge("router", "analysis", condition=should_dispatch_analysis)

    # Workers -> synthesizer (AND-join: fires only when ALL dispatched workers complete)
    builder.add_edge("research", "synthesizer", condition=research_to_synthesizer)
    builder.add_edge("analysis", "synthesizer", condition=analysis_to_synthesizer)

    # Direct path: router -> synthesizer (when no workers are dispatched)
    builder.add_edge("router", "synthesizer", condition=router_direct_to_synthesizer)

    # Timeouts
    builder.set_execution_timeout(execution_timeout)
    builder.set_node_timeout(node_timeout)

    return builder.build()
