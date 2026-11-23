"""Support agent for synthetic orders. Runtime validates the JWT before forwarding it."""

import os
import re
from contextlib import aclosing

import jwt
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from starlette.exceptions import HTTPException
from strands import Agent

from .mcp_client.client import get_streamable_http_mcp_client
from .model.load import load_model

app = BedrockAgentCoreApp()


def identity(context):
    # This is claim extraction ONLY: never expose the container outside its JWT-enforcing Runtime.
    headers = {k.lower(): v for k, v in (getattr(context, "request_headers", {}) or {}).items()}
    auth = headers.get("authorization", "")
    session = getattr(context, "session_id", None)
    if not auth.startswith("Bearer ") or not session or not re.fullmatch(r"[\w-]{33,256}", session):
        raise HTTPException(401, "Authenticated runtime session required")
    token = auth[7:]
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid runtime token") from None
    if not isinstance(claims.get("sub"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", claims["sub"]):
        raise HTTPException(401, "Missing subject")
    return token, claims["sub"], session


@app.entrypoint
async def invoke(payload, context):
    if (
        not isinstance(payload, dict)
        or set(payload) != {"prompt"}
        or not isinstance(payload["prompt"], str)
        or not 1 <= len(payload["prompt"].strip()) <= 4000
    ):
        raise HTTPException(400, "Send one nonempty prompt, up to 4000 characters")
    token, actor, session = identity(context)
    region = os.environ["AWS_REGION"]
    memory = AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=os.environ["BEDROCK_AGENTCORE_MEMORY_ID"],
            session_id=session,
            actor_id=actor,
            retrieval_config={"/facts/{actorId}/": RetrievalConfig(top_k=5, relevance_score=0.5)},
        ),
        region,
    )
    with get_streamable_http_mcp_client(token, region) as gateway:
        agent = Agent(
            model=load_model(),
            session_manager=memory,
            tools=gateway.list_tools_sync(),
            callback_handler=None,
            system_prompt="Use only the demo customer and order tools. All data and refunds are synthetic. Report policy denials accurately and do not retry a denied operation.",
        )
        async with aclosing(agent.stream_async(payload["prompt"])) as stream:
            async for event in stream:
                if isinstance(event.get("data"), str) and event["data"]:
                    yield event["data"]


if __name__ == "__main__":
    app.run()
