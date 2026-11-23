"""Read a key only for this invocation; never provision Identity resources here."""

import os

from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime import BedrockAgentCoreContext
from starlette.exceptions import HTTPException


async def get_api_key() -> str:
    if os.getenv("LOCAL_DEV") == "1":
        if key := os.getenv("GEMINI_API_KEY"):
            return key
        raise HTTPException(503, "Set a local Gemini key before invoking.")

    provider = os.getenv("GEMINI_PROVIDER_NAME")
    if not provider or not BedrockAgentCoreContext.get_workload_access_token():
        raise HTTPException(503, "An Identity API-key provider and workload access token are required.")

    @requires_api_key(provider_name=provider)
    async def receive(*, api_key: str):
        return api_key

    return await receive()
