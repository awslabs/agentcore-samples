from agents import Agent, Runner, WebSearchTool
import logging
import sys

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("openai_agents")

# Configure OpenAI library logging
logging.getLogger("openai").setLevel(logging.DEBUG)

logger.debug("Initializing OpenAI agent with tools")
agent = Agent(
    name="Assistant",
    tools=[
        WebSearchTool(),
    ],
)


async def main(query=None):
    if query is None:
        query = "Which coffee shop should I go to, taking into account my preferences and the weather today in SF?"

    logger.debug(f"Running agent with query: {query}")

    try:
        logger.debug("Starting agent execution")
        result = await Runner.run(agent, query)
        logger.debug(f"Agent execution completed with result type: {type(result)}")
        return result
    except Exception as e:
        logger.error(f"Error during agent execution: {e}", exc_info=True)
        raise


# Integration with Bedrock AgentCore
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
async def agent_invocation(payload, context):
    logger.debug(f"Received payload: {payload}")
    query = payload.get("prompt", "How can I help you today?")

    try:
        result = await main(query)
        logger.debug("Agent execution completed successfully")
        return {"result": result.final_output}
    except Exception as e:
        logger.error(f"Error during agent execution: {e}", exc_info=True)
        return {"result": f"Error: {str(e)}"}


# Run the app when imported
if __name__ == "__main__":
    app.run()
