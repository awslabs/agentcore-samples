from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent
from bedrock_models import Models, global_model_id

app = BedrockAgentCoreApp()
agent = Agent(
    global_model_id(Models.ANTHROPIC_CLAUDE_HAIKU_4_5_20251001, region="eu-west-1"),
)


@app.entrypoint
async def main(message, context):
    if "ping" in message:
        yield {"status": "pong"}
    if "prompt" in message:
        async for event in agent.stream_async(message["prompt"]):
            if "data" in event:
                # Only stream text chunks to the client
                yield event["data"]


app.run()
