import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.exceptions import HTTPException
from strands import Agent, tool
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


# Create a custom tool
@tool
def weather():
    """Return a sample weather forecast (not live weather)."""
    return "sunny"


model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
model = BedrockModel(
    model_id=model_id,
)


@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(user_input, str) or not user_input.strip() or len(user_input) > 4000:
        raise HTTPException(400, "Send a nonempty prompt of at most 4000 characters.")
    # Keep the model transport, not the previous caller's conversation.
    agent = Agent(
        model=model,
        tools=[weather],
        callback_handler=None,
        system_prompt="You are a travel assistant. Use the weather tool when asked about the weather.",
    )
    response = agent(user_input)
    return response.message["content"][0]["text"]
