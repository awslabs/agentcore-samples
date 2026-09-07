"""
AgentCore Runtime entrypoint for the Claims Agent.

All agent modules (agents/, memory/, tools/) live alongside this file
and are packaged together by CodeZip.
"""

import json

from agents.intake_agent import create_intake_agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from memory.config import get_decision_mode, get_memory_client, get_memory_id, get_reviews_api_url, load_config

app = BedrockAgentCoreApp()
log = app.logger

# Load config at module level (cold start).
_config = load_config()
_memory_client = get_memory_client(_config)
_memory_id = get_memory_id(_config)
_reviews_api_url = get_reviews_api_url(_config)
log.info("Initialized — memory: %s, region: %s", _memory_id, _config["region"])


@app.entrypoint
async def invoke(payload, context):
    prompt = payload.get("prompt", "")
    actor_id = payload.get("actorId", "")
    session_id = payload.get("sessionId", "")

    if not prompt or not actor_id or not session_id:
        yield {"event": {"error": "prompt, actorId, and sessionId are required"}}
        return

    decision_mode = get_decision_mode(_config)

    intake = create_intake_agent(
        memory_id=_memory_id,
        memory_client=_memory_client,
        actor_id=actor_id,
        session_id=session_id,
        region=_config["region"],
        mode=decision_mode,
        reviews_api_url=_reviews_api_url,
    )

    result = intake(prompt)
    response_text = str(result)

    event = {
        "event": "message",
        "data": response_text,
        "actor_id": actor_id,
        "session_id": session_id,
        "decision_mode": decision_mode,
    }
    yield {"event": {"contentBlockDelta": {"delta": {"text": json.dumps(event)}}}}


if __name__ == "__main__":
    app.run()
