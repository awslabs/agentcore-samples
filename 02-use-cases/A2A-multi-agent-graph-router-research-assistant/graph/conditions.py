"""Conditional edge functions for the supervisor graph.

This module implements the AND-join pattern for the synthesizer node.

The Strands GraphBuilder fires a target node when ANY incoming edge's condition
returns True (OR semantics). To implement an AND-join (wait for ALL dispatched
workers), every edge feeding the synthesizer independently checks that ALL
dispatched workers have completed. This way the synthesizer fires exactly once —
when the last worker finishes.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from strands.multiagent.base import Status

if TYPE_CHECKING:
    from strands.multiagent.graph import GraphState

# Worker node IDs that can be dispatched (excludes "writer"/synthesizer)
_WORKER_IDS = {"research", "analysis"}


def parse_dispatch_list(router_output: str) -> list[str]:
    """Parse the DISPATCH line from router output to extract worker node IDs.

    Args:
        router_output: The full text output from the router agent.

    Returns:
        List of worker node IDs to dispatch (excludes "writer" since that
        maps to the synthesizer node, which is the final step).
    """
    match = re.search(r"DISPATCH:\s*(.+)", router_output)
    if not match:
        return []

    agents = [a.strip().lower() for a in match.group(1).split(",")]
    return [a for a in agents if a in _WORKER_IDS]


def _get_dispatch_list(state: GraphState) -> list[str]:
    """Extract the dispatch list from the router's result in graph state.

    Args:
        state: The current graph execution state.

    Returns:
        List of worker node IDs the router decided to dispatch.
    """
    router_result = state.results.get("router")
    if router_result is None:
        return []
    return parse_dispatch_list(str(router_result.result))


def _all_dispatched_workers_completed(state: GraphState) -> bool:
    """Check if ALL dispatched worker nodes have completed.

    This is the AND-join guard. It returns True only when every worker
    in the dispatch list has Status.COMPLETED.

    Args:
        state: The current graph execution state.

    Returns:
        True if all dispatched workers are done, False otherwise.
    """
    workers = _get_dispatch_list(state)
    if not workers:
        return False

    for worker_id in workers:
        result = state.results.get(worker_id)
        if result is None or result.status != Status.COMPLETED:
            return False
    return True


def should_dispatch_research(state: GraphState) -> bool:
    """Condition: should the research node be activated?

    Args:
        state: The current graph execution state.

    Returns:
        True if "research" is in the router's dispatch list.
    """
    return "research" in _get_dispatch_list(state)


def should_dispatch_analysis(state: GraphState) -> bool:
    """Condition: should the analysis node be activated?

    Args:
        state: The current graph execution state.

    Returns:
        True if "analysis" is in the router's dispatch list.
    """
    return "analysis" in _get_dispatch_list(state)


def router_direct_to_synthesizer(state: GraphState) -> bool:
    """Condition: should the router skip workers and go directly to synthesizer?

    This handles the case where DISPATCH contains only "writer" — no
    research or analysis workers needed.

    Args:
        state: The current graph execution state.

    Returns:
        True if no workers were dispatched (writer-only path).
    """
    workers = _get_dispatch_list(state)
    return len(workers) == 0


def research_to_synthesizer(state: GraphState) -> bool:
    """AND-join condition on the research -> synthesizer edge.

    Returns True only when ALL dispatched workers have completed.
    This prevents the synthesizer from firing prematurely when research
    finishes before analysis.

    Args:
        state: The current graph execution state.

    Returns:
        True if all dispatched workers are complete.
    """
    return _all_dispatched_workers_completed(state)


def analysis_to_synthesizer(state: GraphState) -> bool:
    """AND-join condition on the analysis -> synthesizer edge.

    Returns True only when ALL dispatched workers have completed.
    Identical logic to research_to_synthesizer — both edges carry
    the same guard so the synthesizer fires exactly once (when the
    last worker completes).

    Args:
        state: The current graph execution state.

    Returns:
        True if all dispatched workers are complete.
    """
    return _all_dispatched_workers_completed(state)
