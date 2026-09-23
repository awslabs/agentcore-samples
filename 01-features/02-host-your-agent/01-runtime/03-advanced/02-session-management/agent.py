"""
Session-aware agent — demonstrates context retention across invocations.

This agent uses Strands with conversation history. When invoked with the
same runtimeSessionId, the microVM persists and the agent remembers
previous messages. Different session IDs get completely isolated environments.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models.bedrock import BedrockModel

model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")

agent = Agent(
    model=model,
    system_prompt=(
        "You are a helpful assistant with a good memory. "
        "Remember everything the user tells you across messages. "
        "When asked about previous information, recall it accurately."
    ),
)

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke_agent(payload: dict) -> str:
    prompt = payload.get("prompt", "Hello!")
    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
