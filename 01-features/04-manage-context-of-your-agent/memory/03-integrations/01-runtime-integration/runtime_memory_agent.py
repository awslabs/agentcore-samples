import os
import logging
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

# Configure detailed logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")

# Initialize the agent core app
app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID")
MEMORY_ID = os.getenv("MEMORY_ID")
REGION = os.getenv("AWS_REGION")

# Global agent instance - will be initialized with first request
agent = None
session_manager = None


def initialize_agent(actor_id, session_id):
    """Initialize the agent with memory session manager"""
    global agent, session_manager

    logger.info(f"Initializing agent for actor_id={actor_id}, session_id={session_id}")

    # Create model
    logger.info(f"Creating model with ID: {MODEL_ID}")
    model = BedrockModel(model_id=MODEL_ID)

    # Configure memory using AgentCoreMemoryConfig (recommended)
    logger.info(f"Creating memory config with region: {REGION}")
    config = AgentCoreMemoryConfig(memory_id=MEMORY_ID, session_id=session_id, actor_id=actor_id)

    # Create session manager - handles all memory operations automatically!
    session_manager = AgentCoreMemorySessionManager(config, region_name=REGION)

    # Create agent with session_manager (replaces custom hooks)
    logger.info("Creating agent with AgentCoreMemorySessionManager")
    agent = Agent(
        model=model,
        session_manager=session_manager,  # ✅ Built-in memory integration
        system_prompt="You're a helpful, memory-enabled agent deployed on AgentCore Runtime. You can remember previous interactions within the same session. Be friendly and concise in your responses.",
    )
    logger.info("✅ Agent initialized with memory session manager")


@app.entrypoint
def runtime_memory_agent(payload, context):
    """
    Main entry point for the memory-enabled agent

    Args:
        payload: The input payload containing user data
        context: The runtime context object containing session information
    """
    global agent, session_manager

    # Log both payload and context info
    logger.info(f"Received payload: {payload}")
    logger.info(f"Context session_id: {context.session_id}")

    # Extract and validate required values
    user_input = payload.get("prompt")
    actor_id = payload.get("actor_id", "default_user")  # Provide default for demo
    session_id = context.session_id  # Get session_id from context

    # Validate required fields
    if user_input is None:
        error_msg = "❌ ERROR: Missing 'prompt' field in payload"
        logger.error(error_msg)
        return error_msg

    # Initialize agent on first request
    if agent is None:
        logger.info("First request - initializing agent")
        initialize_agent(actor_id, session_id)
    else:
        # Check if session or actor changed - need to reinitialize
        current_session = getattr(session_manager, "_session_id", None) if session_manager else None
        if current_session != session_id:
            logger.info(f"Session changed from {current_session} to {session_id} - reinitializing agent")
            initialize_agent(actor_id, session_id)

    # Invoke the agent with the user's input
    logger.info(f"Invoking agent with input: {user_input}")
    response = agent(user_input)
    response_text = response.message["content"][0]["text"]
    logger.info(f"✅ Agent response: {response_text[:50]}...")

    return response_text


if __name__ == "__main__":
    logger.info("Starting AgentCore application")
    app.run()
