"""Notification feed — drain the SQS queue that SNS and EventBridge hooks deliver to.

SNS and EventBridge hooks are fire-and-forget: the harness schedules delivery and
moves on, and the target never returns a decision. This feed shows what actually
arrived downstream, which is not guaranteed to match the hookEvents in the stream
(standard SNS and EventBridge do not preserve order).
"""

import itertools
import json
import threading
import time
from collections import deque

from clients import client

_events: deque = deque(maxlen=300)
_counter = itertools.count(1)
_lock = threading.Lock()
_usage = {"invocations": 0, "inputTokens": 0, "outputTokens": 0}


def _normalize(body: dict) -> dict:
    """SNS raw delivery gives us the hook payload itself; EventBridge wraps it in `detail`."""
    if body.get("source") == "bedrock-agentcore.harness" and "detail" in body:
        channel, payload = "eventbridge", body["detail"]
    else:
        channel, payload = "sns", body

    context = payload.get("context", {})
    event_type = payload.get("event")
    if event_type == "after_invocation":
        usage = context.get("usage", {})
        summary = (
            f"stopReason={context.get('stopReason')} · "
            f"{usage.get('inputTokens', 0)} in / {usage.get('outputTokens', 0)} out tokens"
        )
    elif event_type in ("before_tool_call", "after_tool_call"):
        summary = f"{context.get('toolName')} ({context.get('toolType')})"
        if context.get("error"):
            summary += f" · error: {context['error']}"
    else:
        summary = f"{context.get('messageCount', 0)} message(s)"

    return {
        "channel": channel,
        "hook": payload.get("name"),
        "event": event_type,
        "session_id": payload.get("sessionId"),
        "hook_event_id": payload.get("hookEventId"),
        "summary": summary,
        "payload": payload,
    }


def _poll(queue_url: str):
    sqs = client("sqs")
    while True:
        try:
            resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=10)
        except Exception as e:  # noqa: BLE001 — keep the feed alive across transient errors
            print(f"[notifications] receive failed: {e}", flush=True)
            time.sleep(5)
            continue

        for message in resp.get("Messages", []):
            try:
                item = _normalize(json.loads(message["Body"]))
            except (json.JSONDecodeError, TypeError):
                item = {"channel": "unknown", "summary": message["Body"][:200]}
            item["id"] = next(_counter)
            item["received_at"] = time.time()
            with _lock:
                _events.append(item)
                if item.get("channel") == "eventbridge" and item.get("event") == "after_invocation":
                    usage = item["payload"].get("context", {}).get("usage", {})
                    _usage["invocations"] += 1
                    _usage["inputTokens"] += usage.get("inputTokens", 0)
                    _usage["outputTokens"] += usage.get("outputTokens", 0)
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])


def start(queue_url: str):
    threading.Thread(target=_poll, args=(queue_url,), daemon=True, name="hook-feed").start()


def since(last_id: int) -> dict:
    with _lock:
        return {"events": [e for e in _events if e["id"] > last_id], "usage": dict(_usage)}
