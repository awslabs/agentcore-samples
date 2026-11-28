"""Decode the supported AgentCore HTTP SDK response contracts without logging payloads."""

import json
import re

MAX_BYTES = 1_000_000


def region_for(arn):
    match = re.fullmatch(
        r"arn:aws:bedrock-agentcore:([a-z0-9-]+):[0-9]{12}:runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}", arn
    )
    if not match:
        raise ValueError("Set AGENT_RUNTIME_ARN to a commercial AWS Runtime ARN")
    return match[1]


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("response"), str):
        return value["response"]
    raise ValueError("Unsupported response object; expected text or a response string")


def chunks(client, arn, session, prompt, show_tools=True):
    result = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session,
        contentType="application/json",
        payload=json.dumps({"prompt": prompt}).encode(),
    )
    body = result["response"]
    content_type = result.get("contentType", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "application/json":
            payload = body.read(MAX_BYTES + 1)
            if len(payload) > MAX_BYTES:
                raise ValueError("Response exceeds the demo's size limit")
            yield text(json.loads(payload))
        elif content_type == "text/event-stream":
            # Bound memory even for an unterminated SSE line; don't read the whole body before yielding.
            pending = bytearray()
            data = []
            total = 0
            for block in body.iter_chunks(chunk_size=1024):
                total += len(block)
                if total > MAX_BYTES:
                    raise ValueError("Response exceeds the demo's size limit")
                pending.extend(block)
                while (end := pending.find(b"\n")) >= 0:
                    line = bytes(pending[:end]).removesuffix(b"\r")
                    del pending[: end + 1]
                    if line.startswith(b"data:"):
                        data.append(line[5:].removeprefix(b" "))
                    elif not line and data:
                        part = text(json.loads(b"\n".join(data)))
                        data.clear()
                        if show_tools or not part.lstrip().startswith("🔧 Using tool:"):
                            yield part
            if pending or data:
                raise ValueError("Incomplete streaming event")
        else:
            raise ValueError("Expected application/json or text/event-stream")
    finally:
        body.close()
