"""Strands Decider-based AgentCore evaluator Lambda.

Evaluates agent sessions by calling a self-hosted Strands Decider 2B server.
Unlike Jev (a cloud API requiring an API key), Strands Decider runs entirely
on your own hardware — no external API key or data egress required.

Prerequisites
-------------
1. Start the Strands Decider server on a machine with a GPU or fast CPU:

       docker run --rm -p 8000:8000 \\
           -e MODEL=StrandsAgents/strands-decider-2B-hobson-v21 \\
           public.ecr.aws/strands/decider:latest

   Or install locally:
       pip install strands-decider
       strands-decider serve StrandsAgents/strands-decider-2B-hobson-v21 --port 8000

2. Set the DECIDER_SERVER_URL environment variable on this Lambda function
   to point at the running server, e.g. http://<host>:8000.

Supported evaluators (three AgentCore evaluators backed by one Lambda):
  DeciderGroundedness   TRACE    Are all facts grounded in tool results?
  DeciderHelpfulness    TRACE    How helpful is each agent response?
  DeciderGoalCompletion SESSION  Were all employee goals achieved?

API compatibility
-----------------
Strands Decider exposes the same /v1/systemone HTTP endpoint and JSON schema as
the Jev System One API, making it a self-hostable alternative. The response
format is identical: ``{"answers": {"<name>": {"type": "noul", "noul": 0.87}}}``.

The main difference is that Strands Decider treats ``state`` as a plain-text
string, so this Lambda serializes the structured conversation (turns, tool
calls, responses) into a readable text block before sending it to the server.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Strip run-suffix added by evaluate.py (e.g. "DeciderGroundedness_5488c641" → "DeciderGroundedness")
_SUFFIX_RE = re.compile(r"_[0-9a-f]{8}$")

from spans import Turn, build_turns

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_SERVER_URL = os.environ.get("DECIDER_SERVER_URL", "http://localhost:8000").rstrip("/")
_API_ENDPOINT = f"{_SERVER_URL}/v1/systemone"
_MAX_ATTEMPTS = 4
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

DEFINITIONS = {d["name"]: d for d in json.loads((Path(__file__).parent / "evaluators.json").read_text())["evaluators"]}


class _DeciderError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(code: str, message: str) -> dict[str, str]:
    logger.warning("%s: %s", code, message)
    return {"errorCode": code, "errorMessage": message}


# ---------------------------------------------------------------------------
# HTTP call to Strands Decider server
# ---------------------------------------------------------------------------


def _post(body: bytes, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        _API_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "agentcore-decider-evaluator/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def _ask(state_text: str, questions: dict[str, Any], remaining_ms) -> dict[str, Any]:
    """POST to the Strands Decider server with exponential-backoff retries."""
    body = json.dumps({"state": state_text, "questions": questions}).encode()
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        budget = remaining_ms() / 1000 - 2
        if budget <= 1:
            raise _DeciderError("DECIDER_TIMEOUT", "Lambda deadline reached before Decider responded")
        try:
            return _post(body, timeout=budget)
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")[:500]
            if err.code not in _RETRYABLE_STATUS or attempt == _MAX_ATTEMPTS:
                raise _DeciderError("DECIDER_API_ERROR", f"Decider returned HTTP {err.code}: {detail}") from err
        except (urllib.error.URLError, TimeoutError) as err:
            if attempt == _MAX_ATTEMPTS:
                raise _DeciderError(
                    "DECIDER_UNAVAILABLE",
                    f"Could not reach Decider server at {_SERVER_URL}: {err}",
                ) from err
        delay = min(8.0, 0.5 * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)
        time.sleep(delay)
    raise _DeciderError("DECIDER_UNAVAILABLE", "Decider did not return a result")


# ---------------------------------------------------------------------------
# State serialization — structured turns → plain text for Strands Decider
# ---------------------------------------------------------------------------


def _turn_to_text(turn: Turn) -> str:
    parts = [f"User: {turn.user}"]
    for call in turn.tool_calls:
        inp = json.dumps(call.input)[:300] if call.input is not None else ""
        out = json.dumps(call.output)[:400] if call.output is not None else ""
        parts.append(f"[Tool: {call.name}({inp})] → {out}")
    parts.append(f"Assistant: {turn.assistant_response}")
    return "\n".join(parts)


def _build_state_text(level: str, turns: list[Turn], target_trace_ids: list[str]) -> str:
    """Serialize conversation state to plain text for Strands Decider."""
    if level == "SESSION":
        return "\n\n".join(_turn_to_text(t) for t in turns)

    # TRACE: show conversation history up to (but not including) the target
    # turn, then the target turn in full with tool calls
    matches = [i for i, t in enumerate(turns) if t.trace_id in target_trace_ids]
    position = matches[0] if matches else len(turns) - 1
    parts: list[str] = []
    for t in turns[:position]:
        parts.append(f"User: {t.user}\nAssistant: {t.assistant_response}")
    parts.append(_turn_to_text(turns[position]))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Response interpretation — identical schema to Jev
# ---------------------------------------------------------------------------


def _dist(labels: list[str], probs: list[float]) -> str:
    return ", ".join(f"{lbl} {p:.2f}" for lbl, p in zip(labels, probs))


def _interpret(definition: dict[str, Any], answer: dict[str, Any], model: str) -> dict[str, Any]:
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
        n = len(definition["question"]["criteria"])
        probs = [float(answer["probabilities"].get(str(i), 0.0)) for i in range(n)]
        top = max(range(n), key=probs.__getitem__)
        return {
            "label": labels[top],
            "value": round(float(answer["score"]) / (n - 1), 4),
            "explanation": (
                f"{model}: expected level {float(answer['score']):.2f} of {n - 1}, "
                f"confidence {float(answer.get('confidence', 0)):.2f}. "
                f"Distribution: {_dist(labels, probs)}."
            ),
        }

    if kind == "choice":
        options = list(definition["question"]["criteria"])
        probs = [float(answer["probabilities"].get(o, 0.0)) for o in options]
        expected = sum(p * definition["values"][o] for o, p in zip(options, probs))
        return {
            "label": labels[answer["choice"]],
            "value": round(expected, 4),
            "explanation": (
                f"{model}: confidence {float(answer.get('confidence', 0)):.2f}. "
                f"Distribution: {_dist([labels[o] for o in options], probs)}."
            ),
        }

    raise ValueError(f"Unsupported question type {kind!r}")


# ---------------------------------------------------------------------------
# Evaluator name resolution — same fallback chain as Jev handler
# ---------------------------------------------------------------------------


def _evaluator_name(event: dict[str, Any], context: Any) -> str:
    if event.get("evaluatorName"):
        return _SUFFIX_RE.sub("", event["evaluatorName"])
    if event.get("evaluatorId"):
        name = event["evaluatorId"].rsplit("/", 1)[-1].rsplit("-", 1)[0]
        return _SUFFIX_RE.sub("", name)
    arn_parts = (getattr(context, "invoked_function_arn", "") or "").split(":")
    raw = arn_parts[7] if len(arn_parts) == 8 else ""
    return _SUFFIX_RE.sub("", raw)


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    name = _evaluator_name(event, context)
    definition = DEFINITIONS.get(name)
    if definition is None:
        return _error(
            "UNKNOWN_EVALUATOR",
            f"No Decider definition for evaluator {name!r}. Valid names: " + ", ".join(DEFINITIONS),
        )

    level = event.get("evaluationLevel", "")
    if level == "TOOL_CALL":
        return _error("UNSUPPORTED_LEVEL", "Decider evaluators support TRACE and SESSION levels")

    target = event.get("evaluationTarget") or {}
    target_traces = target.get("traceIds") or []
    raw_spans = (event.get("evaluationInput") or {}).get("sessionSpans") or []
    turns = build_turns(raw_spans)
    if not turns:
        return _error(
            "NO_AGENT_TURNS",
            f"Found no agent turns in {len(raw_spans)} session spans. The agent must "
            "emit GenAI OpenTelemetry spans and message events.",
        )

    if level == "TRACE" and target_traces and not any(t.trace_id in target_traces for t in turns):
        return _error("TARGET_NOT_FOUND", f"No agent turn matches target traces {target_traces}")

    state_text = _build_state_text(level, turns, target_traces)

    try:
        result = _ask(
            state_text,
            {name: definition["question"]},
            context.get_remaining_time_in_millis,
        )
        answer = result["answers"][name]
        output = _interpret(definition, answer, result.get("model", "strands-decider"))
    except _DeciderError as err:
        return _error(err.code, err.message)
    except (KeyError, TypeError, ValueError) as err:
        return _error("DECIDER_BAD_RESPONSE", f"Unexpected Decider response: {err!r}")

    logger.info(
        json.dumps(
            {
                "evaluator": name,
                "level": level,
                "server": _SERVER_URL,
                "model": result.get("model"),
                "latency_ms": result.get("latency_ms"),
                "label": output["label"],
                "value": output["value"],
            }
        )
    )
    return output
