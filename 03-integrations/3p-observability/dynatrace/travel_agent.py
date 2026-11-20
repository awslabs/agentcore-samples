import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


# Create a custom tool
@tool
def weather():
    """Return a sample weather forecast (not live weather)."""
    return "sunny"


model_id = os.getenv("BEDROCK_MODEL_ID", "eu.anthropic.claude-3-7-sonnet-20250219-v1:0")
model = BedrockModel(
    model_id=model_id,
)
agent = Agent(
    model=model,
    tools=[weather],
    system_prompt="You are a travel assistant. Use the weather tool when asked about the weather.",
)


@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt")
    response = agent(user_input)
    return response.message["content"][0]["text"]
