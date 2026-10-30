"""AgentCore Evaluations code-based evaluator that delegates judgment to Jev.

One function serves every evaluator in evaluators.json. AgentCore passes the
evaluator name in each request, and the matching definition supplies the Jev
question and the mapping from Jev's answer to AgentCore's label and value.

``value`` is always the probability-weighted score in [0, 1], so a 55/45 split
between two levels reads as 0.55 rather than a hard 1.0.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

# Strip run-suffix added by evaluate.py (e.g. "JevGroundedness_5488c641" → "JevGroundedness")
_SUFFIX_RE = re.compile(r"_[0-9a-f]{8}$")

import jev
from spans import Turn, build_turns

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFINITIONS = {
    definition["name"]: definition
    for definition in json.loads((Path(__file__).parent / "evaluators.json").read_text())["evaluators"]
}


def _error(code: str, message: str) -> dict[str, str]:
    logger.warning("%s: %s", code, message)
    return {"errorCode": code, "errorMessage": message}


def _references(event: dict[str, Any]) -> dict[str, Any]:
    reference: dict[str, Any] = {}
    for item in event.get("evaluationReferenceInputs") or []:
        expected = (item.get("expectedResponse") or {}).get("text")
        if expected:
            reference["expected_response"] = expected
        assertions = [a.get("text") for a in item.get("assertions") or [] if a.get("text")]
        if assertions:
            reference.setdefault("assertions", []).extend(assertions)
        trajectory = (item.get("expectedTrajectory") or {}).get("toolNames")
        if trajectory:
            reference["expected_tool_sequence"] = trajectory
    return reference


def build_state(
    level: str, turns: list[Turn], target_trace_ids: list[str], reference: dict[str, Any]
) -> dict[str, Any]:
    if level == "SESSION":
        state: dict[str, Any] = {"turns": [turn.as_state() for turn in turns]}
    else:
        matches = [i for i, turn in enumerate(turns) if turn.trace_id in target_trace_ids]
        if target_trace_ids and not matches:
            raise LookupError(f"No agent turn matches target traces {target_trace_ids}")
        position = matches[0] if matches else len(turns) - 1
        state = {
            "previous_turns": [{"user": t.user, "assistant_response": t.assistant_response} for t in turns[:position]],
            "current_turn": turns[position].as_state(),
        }
    if reference:
        state["reference"] = reference
    return state


def _distribution(labels: list[str], probabilities: list[float]) -> str:
    return ", ".join(f"{label} {p:.2f}" for label, p in zip(labels, probabilities))


def interpret(definition: dict[str, Any], answer: dict[str, Any], model: str) -> dict[str, Any]:
    kind = definition["question"]["type"]
    labels = definition["labels"]
    if kind == "noul":
        p_true = float(answer["noul"])
        label = labels["true"] if p_true >= definition.get("threshold", 0.5) else labels["false"]
        return {
            "label": label,
            "value": round(p_true, 4),
            "explanation": f"{model}: P({labels['true']}) = {p_true:.2f}.",
        }

    if kind == "score":
        levels = len(definition["question"]["criteria"])
        probabilities = [float(answer["probabilities"].get(str(i), 0.0)) for i in range(levels)]
        top = max(range(levels), key=probabilities.__getitem__)
        return {
            "label": labels[top],
            "value": round(float(answer["score"]) / (levels - 1), 4),
            "explanation": (
                f"{model}: expected level {float(answer['score']):.2f} of {levels - 1}, "
                f"confidence {float(answer.get('confidence', 0)):.2f}. "
                f"Distribution: {_distribution(labels, probabilities)}."
            ),
        }

    if kind == "choice":
        options = list(definition["question"]["criteria"])
        probabilities = [float(answer["probabilities"].get(o, 0.0)) for o in options]
        expected = sum(p * definition["values"][o] for o, p in zip(options, probabilities))
        return {
            "label": labels[answer["choice"]],
            "value": round(expected, 4),
            "explanation": (
                f"{model}: confidence {float(answer.get('confidence', 0)):.2f}. "
                f"Distribution: {_distribution([labels[o] for o in options], probabilities)}."
            ),
        }

    raise ValueError(f"Unsupported Jev question type {kind!r}")


def _evaluator_name(event: dict[str, Any], context: Any) -> str:
    """Return the base evaluator name (without run suffix) for DEFINITIONS lookup.

    The evaluate.py script appends a ``_{8-hex}`` run suffix to avoid
    ConflictException on re-runs (e.g. ``JevGroundedness_5488c641``). Strip
    it before looking up in DEFINITIONS which uses bare names.

    The documented contract also carries ``evaluatorId`` (the name plus an
    AgentCore-generated suffix such as ``JevGroundedness_5488c641-a1B2c3D4e5``),
    and the Lambda function alias as a final fallback.
    """
    if event.get("evaluatorName"):
        return _SUFFIX_RE.sub("", event["evaluatorName"])
    if event.get("evaluatorId"):
        name = event["evaluatorId"].rsplit("/", 1)[-1].rsplit("-", 1)[0]
        return _SUFFIX_RE.sub("", name)
    arn_parts = (getattr(context, "invoked_function_arn", "") or "").split(":")
    raw = arn_parts[7] if len(arn_parts) == 8 else ""
    return _SUFFIX_RE.sub("", raw)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    name = _evaluator_name(event, context)
    definition = DEFINITIONS.get(name)
    if definition is None:
        return _error(
            "UNKNOWN_EVALUATOR",
            f"No Jev definition for evaluator {name!r}. Invoke the function through "
            "the alias named after the evaluator.",
        )

    level = event.get("evaluationLevel", "")
    target = event.get("evaluationTarget") or {}
    spans = (event.get("evaluationInput") or {}).get("sessionSpans") or []
    turns = build_turns(spans)
    if not turns:
        return _error(
            "NO_AGENT_TURNS",
            f"Found no agent turns in {len(spans)} session spans. The agent must "
            "emit GenAI OpenTelemetry spans and message events.",
        )

    if level == "TOOL_CALL":
        return _error("UNSUPPORTED_LEVEL", "Jev evaluators support TRACE and SESSION levels")
    try:
        state = build_state(level, turns, target.get("traceIds") or [], _references(event))
    except LookupError as error:
        return _error("TARGET_NOT_FOUND", str(error))
    try:
        result = jev.ask(state, {name: definition["question"]}, context.get_remaining_time_in_millis)
        answer = result["answers"][name]
        output = interpret(definition, answer, result.get("model", jev.MODEL))
    except jev.JevError as error:
        return _error(error.code, error.message)
    except (KeyError, TypeError, ValueError) as error:
        return _error("JEV_BAD_RESPONSE", f"Unexpected Jev response: {error!r}")

    logger.info(
        json.dumps(
            {
                "evaluator": name,
                "level": level,
                "traceIds": target.get("traceIds"),
                "model": result.get("model"),
                "usage": result.get("usage"),
                "label": output["label"],
                "value": output["value"],
            }
        )
    )
    return output
