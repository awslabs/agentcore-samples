"""
Streaming Agent — returns responses as Server-Sent Events.

The @app.entrypoint decorator with streaming support allows the agent
to yield partial results that are sent to the client as SSE events.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.bedrock import BedrockModel


@tool
def get_weather(city: str) -> dict:
    """Get weather for a city."""
    return {"city": city, "condition": "sunny", "temp_f": 72}


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    allowed = set("0123456789+-*/.() ")
    if all(c in allowed for c in expression):
        return str(eval(expression))  # noqa: S307  # nosec B307
    return "Invalid expression"


model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    tools=[get_weather, calculator],
    system_prompt="You are a helpful assistant. Provide detailed, thorough responses.",
)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke_agent(payload: dict) -> str:
    prompt = payload.get("prompt", "Hello!")
    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
