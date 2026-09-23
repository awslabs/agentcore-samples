from strands import Agent, tool
from strands_tools import calculator  # Import the calculator tool
from strands.models.litellm import LiteLLMModel
import os
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

os.environ["AZURE_API_KEY"] = "<YOUR_API_KEY>"
os.environ["AZURE_API_BASE"] = "<YOUR_API_BASE>"
os.environ["AZURE_API_VERSION"] = "<YOUR_API_VERSION>"


# Create a custom tool
@tool
def weather():
    """Get weather"""  # Dummy implementation
    return "sunny"


model = "azure/gpt-4.1-mini"
litellm_model = LiteLLMModel(model_id=model, params={"max_tokens": 32000, "temperature": 0.7})


agent = Agent(
    model=litellm_model,
    tools=[calculator, weather],
    system_prompt="You're a helpful assistant. You can do simple math calculation, and tell the weather.",
)


@app.entrypoint
def strands_agent_open_ai(payload):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt")
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
