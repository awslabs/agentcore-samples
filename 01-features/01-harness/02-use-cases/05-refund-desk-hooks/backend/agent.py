"""Agent turn loop — InvokeHarness, surface hook events, and complete inline tool handoffs."""

import json
from collections.abc import Generator

import tools
from clients import agentcore_client

MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = (
    "You are Refund Desk, a customer-support agent for an online electronics store. "
    "You help customers check orders, issue refunds and send confirmation emails.\n\n"
    "Rules:\n"
    "- Always call lookup_order before acting on an order.\n"
    "- When a customer asks for a refund, call issue_refund with the amount they request "
    "(default to the full order total). Company policy is enforced automatically by the "
    "refund system, so do not pre-judge eligibility yourself; just attempt the refund.\n"
    "- If a tool call is blocked by policy, explain the reason to the customer plainly and, "
    "for amounts over the limit, tell them the request has been escalated to a supervisor.\n"
    "- Call one tool at a time. Only call send_email after issue_refund has returned a "
    "refund receipt, then send a short confirmation to the customer's email on file.\n"
    "- Keep replies short and friendly."
)

# Upper bound on inline tool handoffs per user turn, to stop a runaway loop.
MAX_HANDOFFS = 8


def _read_stream(stream, resolved: set) -> Generator[dict, None, None]:
    """Translate one InvokeHarness stream into UI events. The last event is a summary."""
    tool_blocks: dict[int, dict] = {}
    tool_calls: list[dict] = []
    stop_reason = None
    usage = None

    for event in stream:
        if "contentBlockStart" in event:
            block = event["contentBlockStart"]
            start = block.get("start", {})
            if "toolUse" in start:
                call = {
                    "toolUseId": start["toolUse"]["toolUseId"],
                    "name": start["toolUse"]["name"],
                    "input": "",
                }
                tool_blocks[block["contentBlockIndex"]] = call
                tool_calls.append(call)
                yield {"type": "tool_request", "name": call["name"], "tool_use_id": call["toolUseId"]}
            elif "toolResult" in start:
                result = start["toolResult"]
                resolved.add(result["toolUseId"])
                yield {
                    "type": "tool_result",
                    "tool_use_id": result["toolUseId"],
                    "status": result.get("status", "success"),
                }
        elif "contentBlockDelta" in event:
            block = event["contentBlockDelta"]
            delta = block.get("delta", {})
            if "text" in delta:
                yield {"type": "text", "content": delta["text"]}
            elif "toolUse" in delta and block["contentBlockIndex"] in tool_blocks:
                tool_blocks[block["contentBlockIndex"]]["input"] += delta["toolUse"].get("input", "")
            elif "toolResult" in delta:
                for part in delta["toolResult"]:
                    content = part.get("text") if "text" in part else part.get("json")
                    if content is not None:
                        yield {"type": "tool_result_content", "content": content}
        elif "hookEvent" in event:
            hook = event["hookEvent"]
            yield {
                "type": "hook",
                "name": hook["name"],
                "event": hook["type"],
                "decision": hook.get("decision"),
                "reason": hook.get("reason"),
                "hook_event_id": hook["hookEventId"],
            }
        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
            yield {"type": "stop", "reason": stop_reason}
        elif "metadata" in event:
            usage = event["metadata"].get("usage")
            yield {"type": "usage", "usage": usage}
        else:
            for key in ("internalServerException", "validationException", "runtimeClientError"):
                if key in event:
                    yield {"type": "error", "content": event[key].get("message", str(event[key]))}

    yield {"type": "_summary", "stop_reason": stop_reason, "tool_calls": tool_calls}


def run_turn(
    harness_arn: str, session_id: str, message: str, secret: str, tamper: bool = False
) -> Generator[dict, None, None]:
    """Run one user turn, handing inline tool calls to tools.execute until the agent finishes."""
    client = agentcore_client()
    messages = [{"role": "user", "content": [{"text": message}]}]
    pending: list[dict] = []  # inline calls requested by the model, in order
    resolved: set[str] = set()  # toolUseIds that already have a result

    for invocation in range(MAX_HANDOFFS + 1):
        yield {"type": "invocation", "index": invocation, "kind": "user" if invocation == 0 else "tool_result"}
        response = client.invoke_harness(
            harnessArn=harness_arn,
            runtimeSessionId=session_id,
            # Managed memory extracts long-term facts per actor. One actor per session keeps
            # earlier demo runs ("order 1005 was already refunded") from leaking into new ones.
            actorId=session_id,
            messages=messages,
        )

        summary = None
        for event in _read_stream(response["stream"], resolved):
            if event["type"] == "_summary":
                summary = event
            else:
                yield event

        known = {c["toolUseId"] for c in pending}
        pending.extend(c for c in summary["tool_calls"] if c["toolUseId"] not in known)

        if summary["stop_reason"] != "tool_use":
            yield {"type": "done", "stop_reason": summary["stop_reason"]}
            return

        # The harness hands off one inline call at a time: the oldest unresolved one.
        call = next((c for c in pending if c["toolUseId"] not in resolved), None)
        if call is None:
            yield {"type": "error", "content": "Harness stopped for tool_use but no pending inline call was found."}
            return

        try:
            args = json.loads(call["input"] or "{}")
        except json.JSONDecodeError:
            args = {}
        result, status, tampered = tools.execute(call["name"], args, secret, tamper=tamper)
        resolved.add(call["toolUseId"])
        yield {
            "type": "tool_exec",
            "name": call["name"],
            "tool_use_id": call["toolUseId"],
            "input": args,
            "result": result,
            "status": status,
            "tampered": tampered,
        }

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": call["toolUseId"],
                            # The harness rejects {"json": ...} blocks in inline results, so send JSON text.
                            "content": [{"text": json.dumps(result)}],
                            "status": status,
                        }
                    }
                ],
            }
        ]

    yield {"type": "error", "content": f"Stopped after {MAX_HANDOFFS} tool handoffs."}
