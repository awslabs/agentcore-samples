import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from agents import Agent, Runner
from prompt import SYSTEM_PROMPT
from tools import _get_memory_tools, web_search_impl

# Load environment variables from .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY environment variable")

MODEL_ID = os.getenv("MODEL_ID", "gpt-4o-2024-08-06")
MEMORY_ID = os.getenv("MEMORY_ID")
if not MEMORY_ID:
    raise RuntimeError("Missing MEMORY_ID environment variable")


def create_agent(session_id: str, actor_id: str):
    memory_tools = _get_memory_tools(memory_id=MEMORY_ID, session_id=session_id, actor_id=actor_id)
    logger.info(f"Going to add memory tools: {memory_tools}")

    agent_tools = [web_search_impl] + memory_tools

    return Agent(
        name="WebSearch_Agent",
        instructions=SYSTEM_PROMPT,
        model=MODEL_ID,
        tools=agent_tools,
    )


async def _call_agent_stream(agent, prompt: str):
    """
    Call agent using OpenAI Agents SDK Runner with streaming.
    Yields streaming events and final result.
    """
    try:
        logger.info(f"📝 Calling agent with prompt: {prompt[:100]}...")
        logger.info(f"🤖 Agent type: {type(agent)}")
        logger.info(f"🤖 Agent name: {agent.name if hasattr(agent, 'name') else 'unknown'}")

        # Use the proper OpenAI Agents SDK Runner with streaming
        logger.info("🏃 Starting streaming run")

        result = Runner.run_streamed(agent, input=prompt)

        async for event in result.stream_events():
            # Yield each streaming event
            yield {"event": event}

        # After streaming completes, yield the final result
        logger.info("✅ Agent streaming completed")

    except Exception as e:
        logger.error(f"❌ Error running agent: {str(e)}", exc_info=True)
        yield {"error": str(e)}
