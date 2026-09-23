"""
Travel Agent: Strands-based agent with web search capability.
Exposed as a standard AgentCore Runtime endpoint for direct invocation.
"""

from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from ddgs import DDGS
import logging
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")


# --- Tool Definition ---
@tool
def web_search(query: str) -> str:
    """Search the web for travel information."""
    logger.info(f"Searching for: {query}")
    results = DDGS().text(query, max_results=3)
    return "\n".join([f"- {r['title']}: {r['body']}" for r in results])


# --- Agent Definition ---
model = BedrockModel(model_id=MODEL_ID)
agent = Agent(
    name="Travel Agent",
    model=model,
    tools=[web_search],
    system_prompt="You are a travel expert. Help users with destinations, attractions, and travel tips. Use the web_search tool to find current information.",
)


@app.entrypoint
def invoke(payload, context):
    """Main entrypoint for direct invocation."""
    prompt = payload.get("prompt", "")

    session_id = getattr(context, "session_id", "no-session")
    logger.info(f"Travel Agent received: {prompt}, session: {session_id}")

    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
