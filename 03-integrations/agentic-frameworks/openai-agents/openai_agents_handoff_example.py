import logging
import sys
from agents import Agent, WebSearchTool, Runner

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("openai_agents_handoff")

# Configure OpenAI library logging
logging.getLogger("openai").setLevel(logging.DEBUG)

# Create specialized agents for different tasks
travel_agent = Agent(
    name="Travel Expert",
    instructions=(
        "You are a travel expert who helps users plan their trips. "
        "Use web search to find up-to-date information about destinations, "
        "flights, accommodations, and travel requirements. "
        "Provide specific recommendations based on the user's preferences."
    ),
    tools=[WebSearchTool()],
)

food_agent = Agent(
    name="Food Expert",
    instructions=(
        "You are a food expert who helps users find great dining options. "
        "Use web search to find information about restaurants, local cuisine, "
        "food tours, and dietary accommodations. "
        "Provide specific recommendations based on the user's preferences and location."
    ),
    tools=[WebSearchTool()],
)

# Create the main triage agent that can hand off to specialized agents
triage_agent = Agent(
    name="Travel Assistant",
    instructions=(
        "You are a helpful travel assistant. "
        "If the user asks about travel planning, destinations, flights, or accommodations, "
        "hand off to the Travel Expert. "
        "If the user asks about food, restaurants, or dining options, "
        "hand off to the Food Expert. "
        "For general questions, answer directly."
    ),
    handoffs=[travel_agent, food_agent],
)


async def main():
    # Example queries to demonstrate handoffs
    queries = [
        "I'm planning a trip to Japan next month. What should I know?",
        "What are some good restaurants to try in Tokyo?",
        "What's the weather like in San Francisco today?",
    ]

    for query in queries:
        logger.debug(f"Processing query: {query}")
        print(f"\n\n--- QUERY: {query} ---\n")

        try:
            result = await Runner.run(triage_agent, query)
            logger.debug(f"Agent execution completed for query: {query}")
            print(f"FINAL RESPONSE:\n{result.final_output}")

            # Log which agent handled the query
            if hasattr(result, "thread") and result.thread:
                messages = result.thread.messages
                for message in messages:
                    if hasattr(message, "role") and message.role == "assistant":
                        if hasattr(message, "name") and message.name:
                            logger.debug(f"Message from agent: {message.name}")

        except Exception as e:
            logger.error(f"Error processing query '{query}': {e}", exc_info=True)
            print(f"Error: {str(e)}")


# Integration with Bedrock AgentCore
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()


@app.entrypoint
async def agent_invocation(payload, context):
    logger.debug(f"Received payload: {payload}")
    query = payload.get("prompt", "How can I help you with your travel plans?")

    try:
        result = await Runner.run(triage_agent, query)
        logger.debug("Agent execution completed successfully")
        return {"result": result.final_output}
    except Exception as e:
        logger.error(f"Error during agent execution: {e}", exc_info=True)
        return {"result": f"Error: {str(e)}"}


if __name__ == "__main__":
    app.run()
