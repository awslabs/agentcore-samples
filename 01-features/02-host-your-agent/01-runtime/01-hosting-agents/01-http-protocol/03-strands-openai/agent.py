import os
import sys

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models.litellm import LiteLLMModel
from strands_tools import calculator  # Import the calculator tool

app = BedrockAgentCoreApp()

# Azure OpenAI credentials are read from the environment. Set these before running:
#   export AZURE_API_KEY=...        # your Azure OpenAI key
#   export AZURE_API_BASE=...       # e.g. https://<resource>.openai.azure.com
#   export AZURE_API_VERSION=...    # e.g. 2024-08-01-preview
_required = ["AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"]
_missing = [v for v in _required if not os.environ.get(v)]
if _missing:
    sys.exit(
        "Missing required environment variable(s): "
        + ", ".join(_missing)
        + "\nSet AZURE_API_KEY, AZURE_API_BASE, and AZURE_API_VERSION before running "
        "(LiteLLM's azure provider reads these directly)."
    )


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
