"""Stream Strands text deltas through Runtime with a request-local OpenAI client."""

import asyncio
import logging
import os
from contextlib import aclosing

from bedrock_agentcore.identity.auth import requires_api_key
from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext
from openai import AsyncOpenAI
from starlette.exceptions import HTTPException
from strands import Agent, tool
from strands.hooks import BeforeModelCallEvent
from strands.models.openai import OpenAIModel

app = BedrockAgentCoreApp()
# Streaming/parser diagnostics can include prompts or partial tool arguments.
strands_logger = logging.getLogger("strands")
strands_logger.addHandler(logging.NullHandler())
strands_logger.propagate = False


@tool
def weather(city: str) -> str:
    """Return the tutorial forecast for a city (a fixture, not live weather)."""
    return f"Demo forecast for {city}: sunny."


async def get_key():
    if os.getenv("LOCAL_DEV") == "1":
        if key := os.getenv("OPENAI_API_KEY"):
            return key
        raise HTTPException(503, "Provide a local OpenAI key before invoking.")
    provider = os.getenv("OPENAI_PROVIDER_NAME")
    if not provider or not BedrockAgentCoreContext.get_workload_access_token():
        raise HTTPException(503, "An Identity API-key provider and a workload access token are required.")

    @requires_api_key(provider_name=provider)
    async def receive(*, api_key: str):
        return api_key

    return await receive()


async def text_events(prompt, key):
    try:
        async with (
            asyncio.timeout(60),
            AsyncOpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0) as client,
        ):
            model = OpenAIModel(client=client, model_id="gpt-4o", params={"max_tokens": 512})
            agent = Agent(
                model=model,
                tools=[weather],
                callback_handler=None,
                retry_strategy=None,
                system_prompt="Answer concisely. Use weather for a demo forecast and say that it is simulated.",
            )
            steps = 0

            def limit(event: BeforeModelCallEvent):
                nonlocal steps
                steps += 1
                if steps > 4:
                    event.cancel = "Model step limit reached."

            agent.add_hook(limit, BeforeModelCallEvent)
            async with aclosing(agent.stream_async(prompt)) as events:
                async for event in events:
                    if isinstance(event.get("data"), str) and event["data"]:
                        yield event["data"]
    except Exception:  # noqa: BLE001 - Once SSE starts, report a fixed error event, never provider exception details.
        yield {"error": "The response could not be completed."}


@app.entrypoint
async def strands_agent_open_ai(payload):
    prompt = payload.get("prompt", "Hello!") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise HTTPException(400, "Send a nonempty prompt of at most 4000 characters.")
    try:
        key = await get_key()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Do not expose Identity service details.
        raise HTTPException(502, "Unable to obtain the OpenAI credential.") from None
    return text_events(prompt.strip(), key)


if __name__ == "__main__":
    app.run()
