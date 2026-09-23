"""Workflow Contract Goal-Success-Rate evaluator.

Evaluation level: SESSION

Scores a session against a declarative contract of required tool-call
groups. The default contract matches the Market Trends Agent's
documented workflow: the agent must identify the broker, then load or
store a broker profile, then hit at least one market-data tool before
answering any substantive request.

The contract is a list of groups. A group is an OR-set of tool names;
the session passes the group if at least one tool in that set was
called during the session. The session passes overall only if every
group is satisfied AND (optionally) the groups appear in declared
order across the session's tool-call spans.

To customise per-session, pass a list under
event["evaluationInput"]["contract"] shaped like DEFAULT_CONTRACT below;
otherwise the default is used.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Set, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DEFAULT_CONTRACT: List[Dict[str, Any]] = [
    {
        # Strands-based agent uses update_broker_profile / get_broker_profile.
        # LangGraph-based agent uses get_broker_financial_profile /
        # update_broker_financial_interests / parse_broker_profile_from_message.
        "name": "load_or_store_profile",
        "any_of": [
            "update_broker_profile",
            "get_broker_profile",
            "get_broker_financial_profile",
            "update_broker_financial_interests",
            "parse_broker_profile_from_message",
        ],
    },
    {
        "name": "market_data_or_news",
        "any_of": [
            "get_stock_data",
            "search_news",
            "get_market_overview",
            "get_sector_data",
        ],
    },
]


def _tool_name(span: Dict[str, Any]) -> str:
    attrs = span.get("attributes") or {}
    for key in ("gen_ai.tool.name", "tool.name", "traceloop.entity.name"):
        v = attrs.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    name = (span.get("name") or "").strip()
    # Traceloop emits "execute_tool <tool_name>" for every LangGraph tool call.
    if name.startswith("execute_tool "):
        return name[len("execute_tool ") :].strip()
    return name


def _is_tool_call_span(span: Dict[str, Any]) -> bool:
    """True only for *leaf* LangGraph tool-call spans.

    Matches Traceloop / openllmetry's ``execute_tool <name>`` spans or any
    span that exposes ``gen_ai.tool.name``. Avoids matching the LangGraph
    aggregator task spans such as ``execute_task tools``.
    """
    name = span.get("name") or ""
    attrs = span.get("attributes") or {}
    if name.startswith("execute_tool "):
        return True
    if attrs.get("gen_ai.tool.name"):
        return True
    kind = attrs.get("traceloop.span.kind") or ""
    if isinstance(kind, str) and kind.lower() == "tool":
        return True
    return False


def _ordered_tool_calls(spans: Iterable[Dict[str, Any]]) -> List[str]:
    """Return tool names in start-time order, with a stable fallback."""
    tool_spans = [s for s in spans if _is_tool_call_span(s)]

    def _start(span: Dict[str, Any]) -> int:
        v = span.get("startTimeUnixNano") or (span.get("attributes") or {}).get("startTimeUnixNano")
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    tool_spans.sort(key=_start)
    return [_tool_name(s) for s in tool_spans if _tool_name(s)]


def _evaluate_contract(
    calls: List[str], contract: List[Dict[str, Any]]
) -> Tuple[List[Tuple[str, bool]], bool, List[str]]:
    """Return per-group satisfaction, whether ordering holds, and call trace."""
    results: List[Tuple[str, bool]] = []
    any_sets: List[Set[str]] = []
    for group in contract:
        any_of = set(group.get("any_of") or [])
        any_sets.append(any_of)
        results.append((group.get("name") or ",".join(sorted(any_of)), bool(any_of & set(calls))))

    # order check: advance a cursor through calls; each group must be
    # satisfied at or after the previous group's matching index.
    cursor = 0
    ordered = True
    for group_set in any_sets:
        hit = next(
            (i for i in range(cursor, len(calls)) if calls[i] in group_set),
            None,
        )
        if hit is None:
            ordered = False
            break
        cursor = hit + 1
    return results, ordered, calls


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        eval_input = event.get("evaluationInput") or {}
        spans = eval_input.get("sessionSpans") or []
        raw_contract = eval_input.get("contract")
        contract = raw_contract if isinstance(raw_contract, list) and raw_contract else DEFAULT_CONTRACT

        calls = _ordered_tool_calls(spans)
        group_results, ordered, _ = _evaluate_contract(calls, contract)
        satisfied = [name for name, ok in group_results if ok]
        missed = [name for name, ok in group_results if not ok]
        score = len(satisfied) / len(group_results) if group_results else 0.0

        if not missed and ordered:
            label = "PASS"
        elif not missed and not ordered:
            label = "OUT_OF_ORDER"
        elif satisfied:
            label = "PARTIAL"
        else:
            label = "FAIL"

        # Bound call trace length for log safety
        trace_preview = ", ".join(calls[:20]) + (" ..." if len(calls) > 20 else "")
        explanation = (
            f"Contract groups satisfied: {satisfied or 'none'}; "
            f"missed: {missed or 'none'}; ordered={ordered}. "
            f"Tool calls observed: [{trace_preview}]."
        )

        return {"label": label, "value": round(score, 4), "explanation": explanation}
    except Exception as exc:  # noqa: BLE001
        logger.exception("workflow_contract_gsr failed unexpectedly")
        return {
            "errorCode": "EvaluatorInternalError",
            "errorMessage": f"{type(exc).__name__}: {exc}"[:500],
        }
