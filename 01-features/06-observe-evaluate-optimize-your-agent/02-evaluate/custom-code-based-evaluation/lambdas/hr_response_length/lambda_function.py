# pylint: disable=duplicate-code
"""
HRResponseLength — Code-Based Evaluator (TRACE level)

Uses the SDK 1.6 @custom_code_based_evaluator() decorator.
Checks that the agent's response is between MIN_LENGTH and MAX_LENGTH characters.
Strips thinking blocks (<thinking>...</thinking>) before measuring.

Returns:
    value       — 1.0 (PASS) if within range, 0.0 (FAIL) otherwise
    label       — "PASS" or "FAIL"
    explanation — actual length and acceptable range
"""

import re

from bedrock_agentcore.evaluation import (  # pylint: disable=no-name-in-module
    EvaluatorInput,
    EvaluatorOutput,
    custom_code_based_evaluator,
)

MIN_LENGTH = 50
MAX_LENGTH = 600

_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)


def _first_clean_message(span: dict) -> str:
    """Return the first non-empty cleaned message from a span's events."""
    for se in span.get("span_events", []):
        body = se.get("body", {})
        if not isinstance(body, dict):
            continue
        for msg in body.get("output", {}).get("messages", []):
            content = msg.get("content", {})
            if isinstance(content, dict):
                text = content.get("message", "")
                if text:
                    cleaned = _THINKING_RE.sub("", text).strip()
                    if cleaned:
                        return cleaned
    return ""


def _extract_final_response(spans: list) -> str:
    """Extract final visible response text from the invoke_agent span."""
    for span in spans:
        name = (span.get("name") or "").lower()
        if "invoke_agent" not in name:
            continue
        text = _first_clean_message(span)
        if text:
            return text
    return ""


def _extract_fallback_response(spans: list) -> str:
    """Fallback: scan all span_events for any non-empty content message."""
    for span in reversed(spans):
        for se in span.get("span_events", []):
            body = se.get("body", {})
            if not isinstance(body, dict):
                continue
            for msg in (body.get("output", {}) or {}).get("messages", []):
                content = msg.get("content", {})
                text = (content.get("message") or "") if isinstance(content, dict) else ""
                cleaned = _THINKING_RE.sub("", text).strip()
                if cleaned and not cleaned.startswith("[{"):
                    return cleaned
    return ""


@custom_code_based_evaluator()
def lambda_handler(evaluator_input: EvaluatorInput, _context) -> EvaluatorOutput:
    """Evaluate response length for a single agent trace."""
    spans = evaluator_input.session_spans

    # For TRACE level, target_trace_id identifies which trace to evaluate.
    if evaluator_input.evaluation_level == "TRACE" and evaluator_input.target_trace_id:
        spans = [
            s
            for s in spans
            if s.get("traceId") == evaluator_input.target_trace_id
            or s.get("trace_id") == evaluator_input.target_trace_id
        ]

    output_text = _extract_final_response(spans) or _extract_fallback_response(spans)

    if not output_text:
        return EvaluatorOutput(
            label="FAIL",
            errorCode="NoResponseFound",
            errorMessage=(
                f"No agent response text found in {len(spans)} spans. "
                "Expected invoke_agent span with span_events containing output message."
            ),
        )

    length = len(output_text)

    if MIN_LENGTH <= length <= MAX_LENGTH:
        return EvaluatorOutput(
            value=1.0,
            label="PASS",
            explanation=(
                f"Response length {length} chars is within the acceptable range [{MIN_LENGTH}, {MAX_LENGTH}]."
            ),
        )
    if length < MIN_LENGTH:
        return EvaluatorOutput(
            value=0.0,
            label="FAIL",
            explanation=(
                f'Response length {length} chars is too short (minimum {MIN_LENGTH}). Preview: "{output_text[:60]}..."'
            ),
        )
    return EvaluatorOutput(
        value=0.0,
        label="FAIL",
        explanation=(f"Response length {length} chars exceeds maximum {MAX_LENGTH}. Consider a more concise answer."),
    )
