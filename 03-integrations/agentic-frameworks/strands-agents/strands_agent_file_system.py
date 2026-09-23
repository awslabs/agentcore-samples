import os

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent
from strands_tools import file_read, file_write, editor

agent = Agent(tools=[file_read, file_write, editor])

from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
def agent_invocation(payload, context):
    """Handler for agent invocation"""
    user_message = payload.get(
        "prompt",
        "No prompt found in input, please guide customer to create a json payload with prompt key",
    )
    result = agent(user_message)
    print("context:\n-------\n", context)
    print("result:\n*******\n", result)
    return {"result": result.message}


if __name__ == "__main__":
    app.run()
