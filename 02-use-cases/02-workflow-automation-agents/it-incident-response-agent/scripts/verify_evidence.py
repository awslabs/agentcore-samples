"""Pure strict E2E evidence checks. No service calls and no score generation."""

import math
import re
from datetime import UTC, datetime, timezone

EVALUATORS = {"Builtin.Correctness", "Builtin.Helpfulness", "Builtin.ToolSelectionAccuracy", "Builtin.GoalSuccessRate"}
TOOLS = {"lookup_user", "get_process_info", "query_kb", "create_change_request"}


def timestamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing evidence timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # Logs Insights timestamps are UTC.
    return parsed.timestamp()


def verify(tickets, evaluations, started_at, *, require_recurrence=True):
    """Require completed tool results, emission, separate Memory proof and 4 outcomes.

    Scores are outcomes, not a claim that the agent is correct. No quality threshold
    is silently imposed; reviewers should inspect the scores independently.
    """
    if not tickets:
        raise ValueError("No correlated tickets")
    seen_sessions, seen_tools = set(), set()
    first_session = None
    for index, ticket in enumerate(tickets):
        if ticket.get("status") != "Resolved" or not ticket.get("resolution_comment", "").strip():
            raise ValueError("Ticket not resolved with text")
        e = ticket.get("workflow_evidence", {})
        if e.get("ticket_id") != ticket.get("ticket_id"):
            raise ValueError("Ticket evidence mismatch")
        session, trace = e.get("session_id"), e.get("trace_id", "")
        if not session or session in seen_sessions or not re.fullmatch(r"[0-9a-f]{32}", trace) or int(trace, 16) == 0:
            raise ValueError("Missing/duplicate session or trace correlation")
        seen_sessions.add(session)
        if timestamp(e.get("started_at")) < started_at or timestamp(e.get("completed_at")) < timestamp(e["started_at"]):
            raise ValueError("Stale or incomplete workflow evidence")
        if not e.get("event_id"):
            raise ValueError("EventBridge acceptance missing")
        tool_ids = set()
        for tool in e.get("tools", []):
            if (
                tool.get("status") != "success"
                or not tool.get("tool_use_id")
                or tool["tool_use_id"] in tool_ids
                or not re.fullmatch(r"[0-9a-f]{64}", tool.get("result_sha256", ""))
            ):
                raise ValueError("Incomplete/failed/duplicate tool result")
            tool_ids.add(tool["tool_use_id"])
            seen_tools.add(tool["name"].split("___")[-1])
        if not tool_ids:
            raise ValueError("No actual tool results")
        if index == 0:
            first_session = session
        elif require_recurrence:
            expected = f"incidents/{ticket['requester_id']}/{first_session}"
            if not e.get("memory_record_ids") or expected not in e.get("memory_namespaces", []):
                raise ValueError("No cross-session Memory recurrence from this run (DDB count is not Memory proof)")
        matching = [r for r in evaluations if r.get("trace_id") == trace and r.get("session_id") == session]
        completed = set()
        for row in matching:
            if timestamp(row.get("timestamp")) < started_at:
                raise ValueError("Old evaluation result")
            score = row.get("score")
            if row.get("error") or row.get("status") not in (None, "", "Complete", "COMPLETED", "SUCCESS"):
                raise ValueError("Evaluation error/incomplete outcome")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("Evaluation score missing or invalid")
            completed.add(row.get("evaluator"))
        if not EVALUATORS.issubset(completed):
            raise ValueError("All four correlated evaluator outcomes are required")
    if not TOOLS.issubset(seen_tools):
        raise ValueError("Full scenario did not produce all four tool results")
    return {
        "tickets": len(tickets),
        "sessions": len(seen_sessions),
        "evaluators": sorted(EVALUATORS),
        "status": "complete evidence, not quality acceptance",
    }
