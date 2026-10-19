"""Reconstruct agent turns from the ADOT documents AgentCore Evaluations sends.

``evaluationInput.sessionSpans`` mixes two document kinds:

- span documents (``name``, ``attributes``, ``startTimeUnixNano``, ...), and
- log records (``body.input.messages``, ``body.output.messages``) that carry the
  conversation and tool payloads, joined to their span by ``spanId``.

Some producers embed the log records in the span as ``span_events``, and raw
OpenTelemetry exports carry them as ``events`` with GenAI semantic-convention
names. All three shapes are normalized here.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

MAX_FIELD_CHARS = 8_000

_THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)
_INPUT_EVENTS = {"gen_ai.user.message", "gen_ai.assistant.message", "gen_ai.tool.message"}
_OUTPUT_EVENTS = {"gen_ai.choice"}


@dataclass
class ToolCall:
    name: str
    input: Any
    output: Any

    def as_state(self) -> dict[str, Any]:
        return {"tool": self.name, "input": self.input, "output": self.output}


@dataclass
class Turn:
    trace_id: str
    started_ns: int
    user: str = ""
    assistant_response: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "tool_calls": [call.as_state() for call in self.tool_calls],
            "assistant_response": self.assistant_response,
        }


@dataclass
class _Messages:
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)


def _clip(text: str) -> str:
    if len(text) <= MAX_FIELD_CHARS:
        return text
    return text[:MAX_FIELD_CHARS] + f"... [truncated {len(text) - MAX_FIELD_CHARS} chars]"


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def text_of(value: Any) -> str:
    """Return the human-readable text in a GenAI message content value.

    Content arrives as a plain string, a JSON-encoded list of content blocks,
    or nested ``{"content": ...}`` / ``{"message": ...}`` wrappers. Tool-use and
    tool-result blocks are not conversation text and are skipped.
    """
    value = _maybe_json(value)
    if isinstance(value, str):
        return _THINKING.sub("", value).strip()
    if isinstance(value, list):
        return "\n".join(filter(None, (text_of(item) for item in value))).strip()
    if isinstance(value, dict):
        if "toolUse" in value or "toolResult" in value:
            return ""
        for key in ("text", "content", "message"):
            if key in value:
                return text_of(value[key])
    return ""


def _payload(value: Any) -> Any:
    """Return a tool payload as structured data when it is JSON, else as text."""
    value = _maybe_json(value)
    if isinstance(value, dict) and set(value) <= {"content", "message", "role", "id"}:
        inner = value.get("content", value.get("message"))
        if inner is not None:
            return _payload(inner)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        block = value[0]
        if "text" in block:
            return _payload(block["text"])
        if "json" in block:
            return block["json"]
    if isinstance(value, str):
        return _clip(value)
    return value


def _add_body(messages: _Messages, body: Any) -> None:
    if not isinstance(body, dict):
        return
    messages.inputs.extend((body.get("input") or {}).get("messages") or [])
    messages.outputs.extend((body.get("output") or {}).get("messages") or [])


def _add_otel_event(messages: _Messages, event: dict[str, Any]) -> None:
    name = event.get("name")
    attributes = event.get("attributes") or {}
    if name in _INPUT_EVENTS:
        role = name.split(".")[1]
        messages.inputs.append({"role": role, "content": attributes.get("content", "")})
    elif name in _OUTPUT_EVENTS:
        messages.outputs.append({"role": "assistant", "content": attributes.get("message", "")})


def _index(spans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, _Messages]]:
    span_documents: list[dict[str, Any]] = []
    messages: dict[str, _Messages] = defaultdict(_Messages)
    for document in spans:
        if not isinstance(document, dict):
            continue
        span_id = document.get("spanId") or document.get("span_id") or ""
        if "body" in document:
            _add_body(messages[span_id], document["body"])
            continue
        span_documents.append(document)
        for embedded in document.get("span_events") or []:
            _add_body(messages[span_id], embedded.get("body"))
        for event in document.get("events") or []:
            _add_otel_event(messages[span_id], event)
    return span_documents, messages


def _trace_id(document: dict[str, Any]) -> str:
    return document.get("traceId") or document.get("trace_id") or ""


def _operation(document: dict[str, Any]) -> str:
    attributes = document.get("attributes") or {}
    operation = attributes.get("gen_ai.operation.name")
    if operation:
        return operation
    name = (document.get("name") or "").lower()
    for candidate in ("invoke_agent", "execute_tool", "chat"):
        if name.startswith(candidate):
            return candidate
    return ""


def _started(document: dict[str, Any]) -> int:
    return int(document.get("startTimeUnixNano") or 0)


def _last_user_text(messages: _Messages) -> str:
    for message in reversed(messages.inputs):
        if message.get("role") == "user":
            text = text_of(message.get("content"))
            if text:
                return _clip(text)
    return ""


def _first_assistant_text(messages: _Messages) -> str:
    for message in messages.outputs:
        text = text_of(message.get("content"))
        if text:
            return _clip(text)
    return ""


def _tool_call(document: dict[str, Any], messages: _Messages) -> ToolCall:
    attributes = document.get("attributes") or {}
    name = attributes.get("gen_ai.tool.name") or (document.get("name") or "").removeprefix("execute_tool ")
    tool_input = messages.inputs[0].get("content") if messages.inputs else None
    tool_output = messages.outputs[0].get("content") if messages.outputs else None
    return ToolCall(name=name, input=_payload(tool_input), output=_payload(tool_output))


def build_turns(spans: list[dict[str, Any]]) -> list[Turn]:
    """Return one Turn per agent invocation, oldest first."""
    span_documents, messages = _index(spans)
    by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in span_documents:
        by_trace[_trace_id(document)].append(document)

    turns: list[Turn] = []
    for trace_id, documents in by_trace.items():
        documents.sort(key=_started)
        turn = Turn(trace_id=trace_id, started_ns=_started(documents[0]))
        agent_spans = [d for d in documents if _operation(d) == "invoke_agent"]
        chat_spans = [d for d in documents if _operation(d) == "chat"]
        for document in agent_spans or chat_spans[:1]:
            turn.user = turn.user or _last_user_text(messages[document.get("spanId", "")])
        for document in agent_spans or chat_spans[::-1]:
            turn.assistant_response = turn.assistant_response or _first_assistant_text(
                messages[document.get("spanId", "")]
            )
        turn.tool_calls = [
            _tool_call(d, messages[d.get("spanId", "")]) for d in documents if _operation(d) == "execute_tool"
        ]
        if turn.user or turn.assistant_response:
            turns.append(turn)
    turns.sort(key=lambda t: t.started_ns)
    return turns
