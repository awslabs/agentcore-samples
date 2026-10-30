"""after_invocation hook — flag invocations that exceed an output-token budget.

The invocation has already finished when this runs, so a deny cannot retract the
streamed answer. The decision is only reported in the response stream, which makes
this hook a good fit for alerting and chargeback rather than enforcement.
"""

import os


def lambda_handler(event, _context):
    budget = int(os.environ.get("OUTPUT_TOKEN_BUDGET", "300"))
    usage = event.get("context", {}).get("usage", {})
    output_tokens = int(usage.get("outputTokens", 0))

    if output_tokens > budget:
        return {
            "decision": "deny",
            "reason": f"Used {output_tokens} output tokens, over the {budget}-token budget (reported only).",
        }
    return {"decision": "allow", "reason": f"Used {output_tokens}/{budget} output tokens."}
