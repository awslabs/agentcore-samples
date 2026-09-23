from pydantic_ai.agent import Agent, RunContext

from datetime import datetime
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic_ai.models.bedrock import BedrockConverseModel

app = BedrockAgentCoreApp()

model = BedrockConverseModel("global.anthropic.claude-haiku-4-5-20251001-v1:0")
dummy_agent = Agent(
    model=model,
    system_prompt="You're a helpful assistant. Use the tools available for you to answer questions.",
)


@dummy_agent.tool
def get_current_date(ctx: RunContext[datetime]):
    print("Getting current date...")
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return current_date


@dummy_agent.tool
def get_weather(ctx: RunContext[str]):
    # Simulated weather data
    return "Sunny"


@app.entrypoint
def pydantic_bedrock_claude_main(payload):
    """
    Invoke the agent with a payload
    """
    user_input = payload.get("prompt")
    result = dummy_agent.run_sync(user_input)
    print(result.output)
    return result.output


if __name__ == "__main__":
    app.run()
