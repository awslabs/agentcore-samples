"""
Per-session investigation signals collector.

Investigation tools produce structured facts (policy status, coverage
determination, fraud score, claims history). This module captures those
results keyed by session_id so the orchestrator can bundle them into a
review task for the adjuster console (displayed as cards).

All signals are produced and consumed within a single process_claim()
invocation — record() is called by tools during graph execution, and
get() is called immediately after the graph completes, in the same
synchronous call. No signal needs to survive across invocations.

This design requires all graph nodes to run in-process (local Agent
instances). If agents are distributed via A2AAgent (remote runtimes),
signals must move to an external store (e.g. DynamoDB) since remote
tools would write to their own process memory.
"""

from typing import Any

# session_id -> { tool_name: structured_result }
_signals: dict[str, dict[str, Any]] = {}


def record(session_id: str, tool_name: str, structured_result: dict) -> None:
    """Record a tool's structured result for a session (latest-write-wins)."""
    if not session_id:
        return
    _signals.setdefault(session_id, {})[tool_name] = structured_result


def get(session_id: str) -> dict[str, Any]:
    """Return a copy of all recorded signals for a session."""
    return dict(_signals.get(session_id, {}))


def clear(session_id: str) -> None:
    """Drop all recorded signals for a session (call after persisting a task)."""
    _signals.pop(session_id, None)


# ---------------------------------------------------------------------------
# Subtool trace writer — writes tool call details to subtools/ namespace
# for observability in the admin memory inspector.
# ---------------------------------------------------------------------------
import json
import logging

_trace_logger = logging.getLogger("claims-demo.signals.trace")

_trace_client = None
_trace_memory_id = None


def configure_trace(memory_client, memory_id: str) -> None:
    """Set up the subtool trace writer with a memory client and ID.

    Called once by the investigation/adjudication agent factories when mode=auto.
    """
    global _trace_client, _trace_memory_id
    _trace_client = memory_client
    _trace_memory_id = memory_id


def write_subtool_trace(session_id: str, tool_name: str, input_summary: str, output_summary: str) -> None:
    """Write a trace event to the subtools namespace for a tool call.

    Args:
        session_id: The claim session this belongs to.
        tool_name: Name of the tool (e.g. "lookup_policy").
        input_summary: Brief description of what was passed in.
        output_summary: Brief description of what came back.
    """
    if not _trace_client or not _trace_memory_id or not session_id:
        return
    try:
        trace_text = json.dumps(
            {
                "tool": tool_name,
                "query": input_summary,
                "filter": "n/a",
                "result_count": 1,
                "results": [output_summary],
            }
        )
        _trace_client.create_event(
            memory_id=_trace_memory_id,
            actor_id="system",
            session_id=session_id,
            messages=[(trace_text, "TOOL")],
        )
    except Exception as e:  # noqa: BLE001 - best-effort trace; must not break agent flow
        _trace_logger.warning("Failed to write subtool trace: %s", e)
