"""Run one OpenAI Agents SDK turn with a request-local client and credential."""

import os

from agents import Agent, OpenAIResponsesModel, RunConfig, Runner
from agents.exceptions import AgentsException
from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import BedrockAgentCoreContext
from botocore.exceptions import BotoCoreError, ClientError
from openai import AsyncOpenAI, OpenAIError
from starlette.exceptions import HTTPException


async def get_key():
    if os.environ.get("LOCAL_DEV") == "1":
        if key := os.environ.get("OPENAI_API_KEY"):
            return key
        raise HTTPException(503, "Set a local OpenAI key before invoking.")

    provider = os.environ.get("OPENAI_PROVIDER_NAME")
    if not provider or not BedrockAgentCoreContext.get_workload_access_token():
        raise HTTPException(503, "An Identity API-key provider and a workload access token are required.")

    @requires_api_key(provider_name=provider)
    async def receive(*, api_key: str):
        return api_key

    return await receive()


def runtime_app(agent: Agent, default_prompt: str):
    app = BedrockAgentCoreApp()

    @app.entrypoint
    async def invoke(payload):
        if not isinstance(payload, dict):
            raise HTTPException(400, "Send a JSON object.")
        prompt = payload.get("prompt", default_prompt)
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise HTTPException(400, "Send a nonempty prompt of at most 4000 characters.")
        try:
            key = await get_key()
            # Never copy a vended key into the environment or a process-global SDK client.
            async with AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0) as client:
                model = OpenAIResponsesModel(model="gpt-4o", openai_client=client)
                run = await Runner.run(
                    agent, prompt, run_config=RunConfig(model=model, tracing_disabled=True), max_turns=6
                )
            return {"result": run.final_output}
        except HTTPException:
            raise
        except (AgentsException, OpenAIError, BotoCoreError, ClientError, ValueError, RuntimeError):
            # SDK/provider exceptions may contain request data; only expose a fixed message.
            raise HTTPException(502, "Agent request failed. Check Identity access and OpenAI availability.") from None

    return app
