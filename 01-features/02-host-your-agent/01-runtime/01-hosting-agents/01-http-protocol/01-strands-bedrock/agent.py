from strands import Agent, tool
from strands_tools import calculator  # Import the calculator tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


# Create a custom tool
@tool
def weather(city: str):
    """Get weather information.

    Args:
        city: City for which weather will be returned

    Returns:
        Weather of provided city as a string.
    """
    print(city)
    if city.lower() == "athens":
        weather = "very sunny"
    else:
        weather = "sunny"
    return weather


model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
model = BedrockModel(
    model_id=model_id,
)
agent = Agent(
    model=model,
    tools=[calculator, weather],
    system_prompt="You're a helpful assistant. You can do simple math calculation, and tell the weather.",
)


@app.entrypoint
def strands_agent_bedrock(payload):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt")
    print("User input:", user_input)
    response = agent(user_input)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
