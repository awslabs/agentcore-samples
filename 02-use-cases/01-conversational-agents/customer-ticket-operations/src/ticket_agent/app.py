"""AgentCore Runtime entrypoint for the support agent (container, port 8080).

The Runtime's JWT authorizer has verified the caller's Cognito access token before this code runs;
the token arrives in ``context.request_headers`` because the Runtime allowlists ``Authorization``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ticket_agent.agent import bearer, run
from ticket_agent.controls import GATEWAY, Screen, at_least

app = BedrockAgentCoreApp()
log = logging.getLogger("ticket_agent")


def build_controls(stage: str) -> dict[str, Any]:
    """The build's Gateway and in-agent controls, from the Runtime's environment. A build whose
    control is not configured fails the run (KeyError) rather than running without it."""
    controls: dict[str, Any] = {
        "gateway_url": json.loads(os.environ["GATEWAY_URLS"])[GATEWAY[stage]]
    }
    if at_least(stage, "v1"):
        controls["screen"] = Screen(os.environ["GUARDRAIL_ID"], os.environ["GUARDRAIL_VERSION"])
    import boto3

    controls["state_table"] = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
    return controls


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any) -> dict[str, Any]:
    # The second parameter must be named ``context`` for the SDK to pass the request context.
    try:
        token = bearer(getattr(context, "request_headers", None))
    except PermissionError as exc:
        return {"error": str(exc)}
    payload = payload or {}
    try:
        if payload.get("stage") not in GATEWAY:
            return {"error": "explicit valid build required"}
        result = run(
            payload,
            token,
            model_id=os.environ["MODEL_ID"],
            session_id=getattr(context, "session_id", None),
            **build_controls(payload["stage"]),
        )
    except Exception as exc:  # noqa: BLE001 - a failed run returns an error result (no Runtime 500)
        log.exception("run failed")
        return {"error": f"{type(exc).__name__}: {exc}"[:500]}
    log.info(
        "run %s (%s %s): %d tool calls, tripped=%s read_only=%s blocked=%s refused=%s",
        result["run_id"],
        result["stage"],
        result["mode"],
        len(result["tool_calls"]),
        result["halted"],
        result.get("read_only", False),
        result["blocked"],
        bool(result["refused"]),
    )
    return result


if __name__ == "__main__":
    app.run()
