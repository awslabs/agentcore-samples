import os

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent
from strands_tools import calculator

# Initialize our agent without a callback handler
agent = Agent(tools=[calculator], callback_handler=None)

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
async def agent_invocation(payload, context):
    """Handler for agent invocation with streaming support"""
    user_message = payload.get(
        "prompt",
        "No prompt found in input, please guide customer to create a json payload with prompt key",
    )

    print("context:\n-------\n", context)
    print("processing message:\n*******\n", user_message)

    # Get the agent stream
    agent_stream = agent.stream_async(user_message)

    async for event in agent_stream:
        yield event


if __name__ == "__main__":
    app.run()
