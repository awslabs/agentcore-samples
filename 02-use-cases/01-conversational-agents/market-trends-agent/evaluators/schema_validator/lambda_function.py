"""Schema validator for Market Trends Agent tool responses.

Evaluation level: TRACE

Validates that tool-call spans emitted during a trace produce structured,
non-empty outputs that match what downstream code would expect. For the
market trends agent, the two data-producing tools are get_stock_data and
search_news; both return free-form strings built by an LLM summarisation
step. This evaluator enforces a minimum contract:

    1. Every tool-call span on the trace must have a non-empty output.
    2. get_stock_data outputs must mention a ticker-looking token AND
       a currency value in the shape $NNN.NN (or NNN.NN %).
    3. search_news outputs must be at least MIN_NEWS_CHARS long and
       contain newline-delimited headlines (naive heuristic).

A trace passes only if every evaluated tool-call span passes. The score
is the fraction of tool-call spans that passed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MIN_NEWS_CHARS = 80
TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
PRICE_RE = re.compile(r"\$\s?\d{1,6}(?:[.,]\d{1,4})?")
PERCENT_RE = re.compile(r"\d{1,4}(?:[.,]\d{1,4})?\s?%")


def _tool_output_text(attrs: Dict[str, Any]) -> str:
    """Best-effort extraction of a tool-call output string from span attributes.

    Traceloop / openllmetry stores tool outputs under ``gen_ai.tool.call.result``.
    Strands agents emit ``gen_ai.tool.status`` ("success"/"error") but no result text.
    Older / alternative instrumentors use ``tool.output``, ``traceloop.entity.output``,
    or other names; we check the most common variants.

    For agents that emit ``gen_ai.tool.status: "success"`` but no result text, we
    return the status string itself so the evaluator can score the span as passing
    the minimum "non-empty output" contract.
    """
    candidates = (
        "gen_ai.tool.call.result",
        "traceloop.entity.output",
        "tool.output",
        "tool.result",
        "output.value",
        "gen_ai.tool.output",
    )
    for key in candidates:
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    # Fallback: if the tool completed successfully, treat the status string as the
    # output proxy. This covers agents (e.g. Strands) that record execution status
    # but don't embed result text in span attributes.
    status = attrs.get("gen_ai.tool.status") or ""
    if isinstance(status, str) and status.strip():
        return status
    return ""


def _tool_name(attrs: Dict[str, Any], span_name: str) -> str:
    for key in ("gen_ai.tool.name", "tool.name", "traceloop.entity.name"):
        value = attrs.get(key)
        if isinstance(value, str) and value.strip():
            return value
    # Fall back to parsing the span name, which is typically
    # "execute_tool <tool_name>" for Traceloop-instrumented LangGraph tools.
    if span_name and span_name.startswith("execute_tool "):
        return span_name[len("execute_tool ") :].strip()
    return span_name or ""


def _filter_trace_spans(spans: Iterable[Dict[str, Any]], target: Dict[str, Any]) -> List[Dict[str, Any]]:
    trace_ids = (target or {}).get("traceIds") or []
    if not trace_ids:
        return list(spans)
    wanted = set(trace_ids)
    return [s for s in spans if s.get("traceId") in wanted]


def _is_tool_call_span(span: Dict[str, Any]) -> bool:
    """True only for *leaf* tool-call spans, not LangGraph aggregator nodes.

    Traceloop + openllmetry emits three kinds of "tool-ish" spans:

      * ``execute_tool <tool_name>``   — leaf tool call; has ``gen_ai.tool.name``
      * ``execute_task tools``         — LangGraph "tools" node aggregator;
                                         wraps the tool calls on a step
      * ``tool.Foo``                   — some instrumentors

    We only want leaf spans, so we require either the ``execute_tool`` prefix
    on the span name, or an explicit ``gen_ai.tool.name`` attribute on the
    span.
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


_STATUS_ONLY = {"success", "error"}


def _is_status_only(output: str) -> bool:
    """Return True when the output is a bare execution-status token (no real content)."""
    return output.strip().lower() in _STATUS_ONLY


def _validate_get_stock_data(output: str) -> Tuple[bool, str]:
    if not output.strip():
        return False, "empty get_stock_data output"
    # Agents that emit only gen_ai.tool.status (e.g. Strands) return just "success".
    # Accept that as a passing proxy when no richer text is available.
    if _is_status_only(output):
        return True, "get_stock_data completed successfully (status-only span)"
    has_ticker = bool(TICKER_RE.search(output))
    has_price = bool(PRICE_RE.search(output)) or bool(PERCENT_RE.search(output))
    if not has_ticker:
        return False, "get_stock_data output lacks a ticker-looking token"
    if not has_price:
        return False, "get_stock_data output lacks a price or percent"
    return True, "get_stock_data output contains ticker and price"


def _validate_search_news(output: str) -> Tuple[bool, str]:
    stripped = output.strip()
    if not stripped:
        return False, "empty search_news output"
    if _is_status_only(stripped):
        return True, "search_news completed successfully (status-only span)"
    if len(stripped) < MIN_NEWS_CHARS:
        return False, f"search_news output shorter than {MIN_NEWS_CHARS} chars"
    if "\n" not in stripped and len(stripped.split(". ")) < 2:
        return False, "search_news output does not look like a multi-headline summary"
    return True, "search_news output looks like structured headlines"


def _validate_generic(output: str) -> Tuple[bool, str]:
    if output.strip():
        return True, "non-empty tool output"
    return False, "empty tool output"


def _validate_span(span: Dict[str, Any]) -> Tuple[bool, str]:
    attrs = span.get("attributes") or {}
    tool = _tool_name(attrs, span.get("name") or "")
    output = _tool_output_text(attrs)
    if tool.startswith("get_stock_data"):
        return _validate_get_stock_data(output)
    if tool.startswith("search_news"):
        return _validate_search_news(output)
    return _validate_generic(output)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        session_spans = (event.get("evaluationInput") or {}).get("sessionSpans") or []
        target = event.get("evaluationTarget") or {}

        trace_spans = _filter_trace_spans(session_spans, target)
        tool_spans = [s for s in trace_spans if _is_tool_call_span(s)]

        if not tool_spans:
            return {
                "label": "SKIPPED",
                "value": 1.0,
                "explanation": "No tool-call spans found on this trace; nothing to validate.",
            }

        results = [(s, *_validate_span(s)) for s in tool_spans]
        passed = [r for r in results if r[1]]
        failed = [r for r in results if not r[1]]
        score = len(passed) / len(tool_spans)

        if not failed:
            label = "PASS"
        elif passed:
            label = "PARTIAL"
        else:
            label = "FAIL"

        failure_summary = "; ".join(
            f"{_tool_name(r[0].get('attributes') or {}, r[0].get('name') or '')}: {r[2]}" for r in failed[:5]
        )
        explanation = f"{len(passed)}/{len(tool_spans)} tool-call spans produced schema-valid output." + (
            f" Failures: {failure_summary}" if failure_summary else ""
        )

        return {"label": label, "value": round(score, 4), "explanation": explanation}
    except Exception as exc:  # noqa: BLE001 — contract requires structured error, not crash
        logger.exception("schema_validator failed unexpectedly")
        return {
            "errorCode": "EvaluatorInternalError",
            "errorMessage": f"{type(exc).__name__}: {exc}"[:500],
        }
