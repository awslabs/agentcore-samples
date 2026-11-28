"""A request-local ADK agent with Gemini's hosted Google Search tool."""

import asyncio
import logging
import os
import uuid
from contextlib import AsyncExitStack

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig
from google.adk.models import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import google_search
from google.genai import Client, types
from provider_credentials import get_api_key
from starlette.exceptions import HTTPException

APP_NAME = "google_search_agent"
MODEL_ID = "gemini-3.5-flash-lite"
# ADK logs raw provider exceptions in both its node executor and runner. Keep
# that library's records out of this sample's logs; Runtime logs the fixed status.
adk_logger = logging.getLogger("google_adk")
adk_logger.addHandler(logging.NullHandler())
adk_logger.propagate = False
app = BedrockAgentCoreApp()


async def search(query: str, user_id: str, session_id: str, include_grounding: bool):
    key = await get_api_key()
    async with AsyncExitStack() as cleanup:
        client = Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(base_url="https://generativelanguage.googleapis.com"),
        )
        cleanup.callback(client.close)
        cleanup.push_async_callback(client.aio.aclose)
        agent = Agent(
            model=Gemini(model=os.getenv("GEMINI_MODEL_ID", MODEL_ID), client=client),
            name=APP_NAME,
            instruction="Use Google Search for questions that need current information. Distinguish search results from your own conclusions.",
            tools=[google_search],
        )
        sessions = InMemorySessionService()
        await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
        cleanup.push_async_callback(runner.close)
        content = types.Content(role="user", parts=[types.Part(text=query)])
        events = runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content, run_config=RunConfig(max_llm_calls=4)
        )
        cleanup.push_async_callback(events.aclose)
        async for event in events:
            if not event.is_final_response() or not event.content:
                continue
            text = "".join(p.text or "" for p in event.content.parts or [] if not p.thought)
            if text.strip():
                if include_grounding:
                    metadata = event.grounding_metadata
                    return {
                        "answer": text,
                        "grounding": metadata.model_dump(mode="json", exclude_none=True) if metadata else None,
                    }
                return text
        raise HTTPException(502, "Gemini returned no final text.")


@app.entrypoint
async def agent_invocation(payload, context):
    if not isinstance(payload, dict):
        raise HTTPException(400, "Send a JSON object.")
    query = payload.get("prompt", "What is Amazon Bedrock AgentCore Runtime?")
    user = payload.get("user_id", "demo-user")  # ADK session label only; not an authenticated identity.
    grounding = payload.get("include_grounding", False)
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise HTTPException(400, "Send a nonempty prompt of at most 4000 characters.")
    if not isinstance(user, str) or not user.strip() or len(user) > 128 or not isinstance(grounding, bool):
        raise HTTPException(400, "Invalid user_id or include_grounding option.")
    try:
        async with asyncio.timeout(60):
            return await search(query.strip(), user, context.session_id or str(uuid.uuid4()), grounding)
    except HTTPException:
        raise
    except TimeoutError:
        raise HTTPException(504, "Search timed out.") from None
    except Exception:  # noqa: BLE001 - Third-party exceptions may contain provider keys or request data.
        raise HTTPException(502, "Search failed. Check Identity access, model availability, and quota.") from None


if __name__ == "__main__":
    app.run()
