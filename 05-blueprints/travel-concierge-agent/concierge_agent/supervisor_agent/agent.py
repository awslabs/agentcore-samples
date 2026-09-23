import os
import logging
import boto3
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
import json
import traceback

# Import local modules
from prompt_manager import get_prompt
from dynamodb_manager import DynamoDBManager
from gateway_client import get_gateway_client
from cart_subagent import cart_manager
from travel_subagent import travel_assistant

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# Enable CORS for local development
app.cors_allow_origins = ["http://localhost:3000", "http://localhost:5173"]
app.cors_allow_methods = ["GET", "POST", "OPTIONS"]
app.cors_allow_headers = ["Content-Type", "Authorization"]

REGION = os.getenv("AWS_REGION")
MEMORY_ID = os.getenv("MEMORY_ID")
ITINERARY_TABLE_NAME = os.getenv("ITINERARY_TABLE_NAME")

logger.info(f"🗂️  Using Itinerary table: {ITINERARY_TABLE_NAME}")

# DynamoDB setup for itinerary
dynamodb = boto3.resource("dynamodb", region_name=REGION)

# Initialize bedrock model - Claude 4.5 Sonnet for routing/coordination
bedrock_model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    region_name=REGION,
    temperature=0.1,
)


def get_user_profile_data(user_id: str) -> str:
    """Get user profile data formatted for prompts."""
    try:
        manager = DynamoDBManager(region_name=REGION)
        profile = manager.get_user_profile(user_id)

        if not profile:
            return "User profile not available"

        # Extract profile information
        profile_parts = []

        # IMPORTANT: Always include userId first - this is the user's unique identifier
        if profile.get("userId"):
            profile_parts.append(f"User ID (use this for all tool calls): {profile['userId']}")

        if profile.get("name"):
            profile_parts.append(f"Name: {profile['name']}")

        if profile.get("email"):
            profile_parts.append(f"Email: {profile['email']}")

        if profile.get("address"):
            profile_parts.append(f"Address: {profile['address']}")

        if profile.get("notes"):
            profile_parts.append(f"Notes: {profile['notes']}")

        if profile.get("preferences"):
            preferences = profile["preferences"]
            if isinstance(preferences, str):
                try:
                    prefs = json.loads(preferences)
                    profile_parts.append(f"Preferences: {json.dumps(prefs)}")
                except json.JSONDecodeError:
                    profile_parts.append(f"Preferences: {preferences}")
            else:
                profile_parts.append(f"Preferences: {preferences}")

        if profile.get("onboardingCompleted"):
            profile_parts.append(f"Onboarding completed: {profile['onboardingCompleted']}")

        if profile_parts:
            profile_text = f", Profile: {'; '.join(profile_parts)}"
        else:
            profile_text = ", Profile: Basic user profile available"

        return profile_text

    except Exception as e:
        logger.error(f"Error getting user profile: {e}")
        return "User profile not available"


def create_supervisor_agent(user_id: str, session_id: str) -> Agent:
    """Create supervisor agent with AgentCore memory session manager."""
    # Get user profile data
    try:
        user_profile = get_user_profile_data(user_id)
        logger.info(f"Retrieved user profile for {user_id}: {user_profile[:200]}...")
    except Exception as e:
        logger.warning(f"Could not retrieve user profile for {user_id}: {e}")
        user_profile = "User profile not available"

    # Get base prompt and add user profile context
    base_prompt = get_prompt("travel_agent_supervisor").format(user_profile=user_profile)

    # Configure AgentCore Memory integration
    agentcore_memory_config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID, session_id=session_id, actor_id=f"supervisor-{user_id}"
    )

    session_manager = AgentCoreMemorySessionManager(agentcore_memory_config=agentcore_memory_config, region_name=REGION)

    logger.info("Creating supervisor agent with session manager...")

    # Get Gateway MCP client for itinerary tools
    logger.info("Setting up Gateway MCP client...")
    itinerary_client = get_gateway_client("^itinerarytools___")

    # Create agent with itinerary tools and subagents
    agent = Agent(
        name="supervisor_agent",
        system_prompt=base_prompt,
        tools=[itinerary_client, cart_manager, travel_assistant],
        model=bedrock_model,
        session_manager=session_manager,
        trace_attributes={
            "user.id": user_id,
            "session.id": session_id,
        },
    )
    logger.info("✅ Agent created with itinerary tools and subagents (cart + travel)")

    logger.info("Supervisor agent created successfully with session manager")
    return agent


@app.entrypoint
async def agent_stream(payload):
    """Main entrypoint for the supervisor agent with session manager."""
    user_query = payload.get("prompt")
    user_id = payload.get("user_id")
    session_id = payload.get("session_id")

    if not all([user_query, user_id, session_id]):
        yield {
            "status": "error",
            "error": "Missing required fields: prompt, user_id, or session_id",
        }
        return

    try:
        logger.info(f"Starting streaming invocation for user: {user_id}, session: {session_id}")
        logger.info(f"Query: {user_query}")

        agent = create_supervisor_agent(user_id, session_id)

        # Use the agent's stream_async method for true token-level streaming
        async for event in agent.stream_async(user_query):
            yield event

    except Exception as e:
        logger.error(f"Error in agent_stream: {e}")
        traceback.print_exc()
        yield {"status": "error", "error": str(e)}


if __name__ == "__main__":
    app.run()
