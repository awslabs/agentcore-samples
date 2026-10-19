"""Lifecycle hook definitions and the UpdateHarness call that applies them.

Each entry maps one lifecycle event to one target. Together they cover all four
events and all three target types, and each Lambda hook shows a different deny effect:

  before_invocation  deny -> invocation never starts (stopReason: hook_stopped)
  before_tool_call   deny -> only that tool call is skipped, the loop continues
  after_tool_call    deny -> tool result is kept, then the invocation stops
  after_invocation   deny -> reported only, streamed output cannot be retracted
"""

import time

from clients import agentcore_control_client

# "field" is the HarnessHook union member; "arn_key" names the target ARN in resource_info.json.
HOOK_DEFINITIONS = [
    {
        "name": "screen_request",
        "event": "before_invocation",
        "field": "beforeInvocation",
        "target": "lambda",
        "arn_key": "screen_request_arn",
        "timeout": 3,
        "description": "Blocks prompt-injection attempts before the agent starts.",
    },
    {
        "name": "refund_policy",
        "event": "before_tool_call",
        "field": "beforeToolCall",
        "target": "lambda",
        "arn_key": "refund_policy_arn",
        "timeout": 5,
        "description": "Skips refunds over $500, refunds on undelivered or refunded orders, and emails to non-customers.",
    },
    {
        "name": "validate_result",
        "event": "after_tool_call",
        "field": "afterToolCall",
        "target": "lambda",
        "arn_key": "validate_result_arn",
        "timeout": 5,
        "description": "Verifies the HMAC signature on client-supplied refund receipts.",
    },
    {
        "name": "audit_tool_calls",
        "event": "after_tool_call",
        "field": "afterToolCall",
        "target": "sns",
        "arn_key": "audit_topic_arn",
        "description": "Publishes every tool result to an SNS audit topic.",
    },
    {
        "name": "token_budget",
        "event": "after_invocation",
        "field": "afterInvocation",
        "target": "lambda",
        "arn_key": "token_budget_arn",
        "timeout": 5,
        "description": "Flags invocations over the output-token budget (report only).",
    },
    {
        "name": "usage_meter",
        "event": "after_invocation",
        "field": "afterInvocation",
        "target": "eventBridge",
        "arn_key": "event_bus_arn",
        "description": "Sends token usage to an EventBridge bus for metering.",
    },
]

HOOKS_BY_NAME = {h["name"]: h for h in HOOK_DEFINITIONS}


def default_settings() -> dict:
    return {h["name"]: {"enabled": True, "failureMode": "deny"} for h in HOOK_DEFINITIONS}


def build_hooks(state: dict, settings: dict) -> list[dict]:
    """Translate UI settings into the HarnessHooks list accepted by Create/UpdateHarness."""
    hooks = []
    for hook in HOOK_DEFINITIONS:
        setting = settings.get(hook["name"], {})
        if not setting.get("enabled", True):
            continue
        arn = state[hook["arn_key"]]
        if hook["target"] == "lambda":
            target = {
                "lambda": {
                    "arn": arn,
                    "timeoutSeconds": hook["timeout"],
                    "failureMode": setting.get("failureMode", "deny"),
                }
            }
        else:
            target = {hook["target"]: {"arn": arn}}
        hooks.append({hook["field"]: {"name": hook["name"], "target": target}})
    return hooks


def wait_for_harness(harness_id: str, timeout: int = 600) -> dict:
    control = agentcore_control_client()
    deadline = time.monotonic() + timeout
    while True:
        harness = control.get_harness(harnessId=harness_id)["harness"]
        status = harness["status"]
        if status == "READY":
            return harness
        if status.endswith("_FAILED"):
            raise RuntimeError(f"Harness {status}: {harness.get('failureReason', '')}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Harness not READY after {timeout}s (status: {status})")
        time.sleep(3)


def apply_hooks(state: dict, settings: dict) -> list[dict]:
    """Replace the harness hook configuration. UpdateHarness swaps the whole list."""
    hooks = build_hooks(state, settings)
    agentcore_control_client().update_harness(harnessId=state["harness_id"], hooks=hooks)
    wait_for_harness(state["harness_id"])
    return hooks
