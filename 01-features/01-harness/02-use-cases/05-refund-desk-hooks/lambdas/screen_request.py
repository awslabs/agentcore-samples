"""before_invocation hook — screen the incoming request before the agent starts.

A deny here stops the invocation before the agent loop runs; the response stream
ends with messageStop.stopReason == "hook_stopped".

CHAOS_MODE lets the web app simulate a broken hook so you can watch failureMode
take over: "slow" sleeps past the hook's timeoutSeconds, "error" raises.
"""

import os
import re
import time

INJECTION_PATTERNS = [
    r"ignore (all |any |your )?(previous |prior )?instructions",
    r"disregard (all |your )?(previous |prior )?(instructions|rules|policy)",
    r"reveal (your |the )?system prompt",
    r"you are now",
    r"developer mode",
    r"bypass (the )?(refund )?(policy|limit)",
]


def _latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        texts = [block["text"] for block in message.get("content", []) if "text" in block]
        if texts:
            return "\n".join(texts)
    return ""


def lambda_handler(event, _context):
    chaos = os.environ.get("CHAOS_MODE", "off")
    if chaos == "slow":
        time.sleep(10)
    elif chaos == "error":
        raise RuntimeError("Simulated screening service outage")

    hook_context = event.get("context", {})
    if hook_context.get("truncated"):
        return {"decision": "deny", "reason": "Request too large to screen completely."}

    text = _latest_user_text(hook_context.get("messages", []))
    if not text:
        # Follow-up invocations that only return an inline toolResult carry no user text.
        return {"decision": "allow", "reason": "No user text to screen (tool result resume)."}

    lowered = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return {
                "decision": "deny",
                "reason": f"Possible prompt injection detected (matched: '{pattern}').",
            }

    return {"decision": "allow", "reason": "Request passed screening."}
