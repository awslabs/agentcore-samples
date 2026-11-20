"""Decode AgentCore Runtime responses into employee-facing text."""

from __future__ import annotations

import json
import re
from typing import Any

_INTERNAL_BLOCK = re.compile(
    r"<(?P<tag>think|thinking|analysis|reasoning)\b[^>]*>.*?</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)
_WRAPPER_TAG = re.compile(r"</?(?:response|final)\b[^>]*>", re.IGNORECASE)


def _sanitize(text: str) -> str:
    """Remove internal reasoning blocks and user-facing wrapper tags."""
    text = _INTERNAL_BLOCK.sub("", text)
    return _WRAPPER_TAG.sub("", text).strip()


def decode_agentcore_response(response: dict[str, Any]) -> str:
    """Decode a plain or Server-Sent Events AgentCore response."""
    body = response["response"]
    content_type = response.get("contentType", "")
    if "text/event-stream" not in content_type:
        return _sanitize(body.read().decode("utf-8", errors="replace"))

    fragments: list[str] = []
    for raw_line in body.iter_lines():
        line = raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        payload = line[5:].lstrip()
        if not payload or payload == "[DONE]":
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            value = payload
        if isinstance(value, str):
            fragments.append(value)
        elif isinstance(value, dict) and isinstance(value.get("data"), str):
            fragments.append(value["data"])
    return _sanitize("".join(fragments))
