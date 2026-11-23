"""Optional streaming producer: one request-local agent and an explicitly simulated forecast."""

import os
from contextlib import aclosing

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

app = BedrockAgentCoreApp()


@tool
def weather() -> str:
    """Return the demo weather, not a live forecast."""
    return "Sunny (simulated)"


@app.entrypoint
async def strands_agent_bedrock(payload):
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("Expected a nonempty prompt of up to 4000 characters")
    agent = Agent(model=BedrockModel(model_id=os.environ["BEDROCK_MODEL_ID"]), tools=[weather], callback_handler=None)
    async with aclosing(agent.stream_async(prompt)) as events:
        last_tool = None
        async for event in events:
            current = event.get("current_tool_use", {})
            if current.get("name") and current.get("toolUseId") != last_tool:
                last_tool = current["toolUseId"]
                yield f"\n\n🔧 Using tool: {current['name']}\n\n"
            if isinstance(event.get("data"), str) and event["data"]:
                yield event["data"]


if __name__ == "__main__":
    app.run()
