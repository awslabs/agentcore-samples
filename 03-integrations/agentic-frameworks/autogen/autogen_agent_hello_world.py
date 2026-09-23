from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("autogen_agent")

print(1)
# Adapted from https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/quickstart.html
# Define a model client. You can use other model client that implements
# the `ChatCompletionClient` interface.

# Define a model client. You can use other model client that implements
# the `ChatCompletionClient` interface.
model_client = OpenAIChatCompletionClient(
    model="gpt-4o",
)

print(2)


# Define a simple function tool that the agent can use.
# For this example, we use a fake weather tool for demonstration purposes.
async def get_weather(city: str) -> str:
    """Get the weather for a given city."""
    print("tool")
    return f"The weather in {city} is 73 degrees and Sunny."


# Define an AssistantAgent with the model, tool, system message, and reflection enabled.
# The system message instructs the agent via natural language.
agent = AssistantAgent(
    name="weather_agent",
    model_client=model_client,
    tools=[get_weather],
    system_message="You are a helpful assistant.",
    reflect_on_tool_use=True,
    model_client_stream=True,  # Enable streaming tokens from the model client.
)

print(4)

# Run the agent and stream the messages to the console.


from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
async def main(payload):
    logger.debug("Starting agent execution")
    print(5)

    try:
        # Get prompt from payload or use default
        prompt = payload.get("prompt", "Hello! What can you help me with?")
        logger.debug(f"Processing prompt: {prompt}")

        # Run the agent
        result = await Console(agent.run_stream(task=prompt))
        logger.debug(f"Agent result type: {type(result)}")
        print(result)

        # Extract the last message content for JSON serialization
        if result and hasattr(result, "messages") and result.messages:
            last_message = result.messages[-1]
            logger.debug(f"Last message: {last_message}")
            if hasattr(last_message, "content"):
                response = {"result": last_message.content}
                logger.debug(f"Returning response: {response}")
                return response

        # Fallback if we can't extract content
        logger.warning("Could not extract content from result")
        return {"result": "No response generated"}
    except Exception as e:
        logger.error(f"Error in main handler: {e}", exc_info=True)
        return {"result": f"Error: {str(e)}"}
    finally:
        # Always close the connection to the model client
        logger.debug("Closing model client connection")
        # await model_client.close() ## Do not close client with sticky sessions on runtime, otherwise you will get `RuntimeError: Cannot send a request, as the client has been closed.`


if __name__ == "__main__":
    app.run()
