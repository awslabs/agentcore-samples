import os
import jwt
import logging
from strands import Agent
from jwt import PyJWKClient
from strands.models import BedrockModel
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)

# Configure detailed logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")

# Initialize the agent core app
app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID")
MEMORY_ID = os.getenv("MEMORY_ID")
COGNITO_USER_POOL = os.getenv("COGNITO_USER_POOL")
REGION = os.getenv("AWS_REGION")

# Global agent instance - will be initialized with first request
agent = None


class MemoryHookProvider(HookProvider):
    """Custom hook provider to integrate with Bedrock Memory"""

    def __init__(self, region_name):
        logger.info(f"Initializing MemoryHookProvider with region {region_name}")
        self.memory_client = MemoryClient(region_name=region_name)

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        logger.info("Agent initialization hook triggered")

        memory_id = event.agent.state.get("memory_id")
        actor_id = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")

        logger.info(f"State values - memory_id: {memory_id}, actor_id: {actor_id}, session_id: {session_id}")

        missing_values = []
        if not memory_id:
            missing_values.append("memory_id")
        if not actor_id:
            missing_values.append("actor_id")
        if not session_id:
            missing_values.append("session_id")

        if missing_values:
            logger.warning(f"Missing required values: {', '.join(missing_values)}")
            return

        try:
            # First, check if the session exists by listing events with a limit of 1
            logger.info(f"Checking if session {session_id} exists...")
            session_exists = False
            try:
                events = self.memory_client.list_events(
                    memory_id=memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    max_results=1,
                )
                session_exists = len(events) > 0
                logger.info(f"Session exists: {session_exists} (found {len(events)} events)")
            except Exception as e:
                logger.warning(f"Error checking session existence: {e}")
                # Assume no events exist and continue
                session_exists = False

            # If session doesn't exist, no need to load conversation history
            if not session_exists:
                logger.info(f"No existing conversation found for session {session_id}")
                return

            # Session exists, load the conversation history
            logger.info(f"Loading conversation history for existing session {session_id}")
            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=memory_id, actor_id=actor_id, session_id=session_id, k=5
            )

            if recent_turns:
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns from memory")
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"]
                        content = message["content"]["text"]
                        context_messages.append(f"{role}: {content}")

                context = "\n".join(context_messages)
                event.agent.system_prompt += f"\n\nRecent conversation:\n{context}"
                logger.info("✅ Added conversation context to system prompt")
            else:
                logger.info("No recent turns found for this session")

        except Exception as e:
            logger.error(f"❌ Memory load error: {e}", exc_info=True)

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory"""
        logger.info("Message added hook triggered")

        memory_id = event.agent.state.get("memory_id")
        actor_id = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")

        logger.info(f"State values - memory_id: {memory_id}, actor_id: {actor_id}, session_id: {session_id}")

        missing_values = []
        if not memory_id:
            missing_values.append("memory_id")
        if not actor_id:
            missing_values.append("actor_id")
        if not session_id:
            missing_values.append("session_id")

        if missing_values:
            logger.warning(f"❌ Cannot save message - missing values: {', '.join(missing_values)}")
            return

        messages = event.agent.messages
        try:
            last_message = messages[-1]
            message_content = str(last_message.get("content", ""))
            message_role = last_message["role"]

            logger.info(f"Saving {message_role} message to memory: {message_content[:30]}...")

            self.memory_client.create_event(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(message_content, message_role)],
            )
            logger.info("✅ Message saved to memory successfully")
        except Exception as e:
            logger.error(f"❌ Memory save error: {e}", exc_info=True)

    def register_hooks(self, registry: HookRegistry):
        logger.info("Registering memory hooks")
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


def initialize_agent(actor_id, session_id):
    """Initialize the agent for first use"""
    global agent

    logger.info(f"Initializing agent for actor_id={actor_id}, session_id={session_id}")

    # Create model and memory hook
    logger.info(f"Creating model with ID: {MODEL_ID}")
    model = BedrockModel(model_id=MODEL_ID)
    logger.info(f"Creating memory hook with region: {REGION}")
    memory_hook = MemoryHookProvider(region_name=REGION)

    # Create agent with proper initial state
    logger.info("Creating agent with memory hook")
    agent = Agent(
        model=model,
        hooks=[memory_hook],
        system_prompt="You're a helpful, memory-enabled agent deployed on AgentCore Runtime. You can remember previous interactions within the same session. Be friendly and concise in your responses.",
        state={"memory_id": MEMORY_ID, "actor_id": actor_id, "session_id": session_id},
    )
    logger.info(f"✅ Agent initialized with state: {agent.state.get()}")


def get_user_sub(access_token: str, region: str, user_pool_id: str) -> str:
    """
    Verifies a Cognito access token against JWKS and returns the user's sub (unique ID).

    :param access_token: The JWT access token string
    :param region: AWS region of the Cognito User Pool
    :param user_pool_id: The Cognito User Pool ID
    :return: The user's 'sub' claim if the token is valid
    :raises jwt.InvalidTokenError: If verification fails
    """
    access_token = access_token[7:]
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
    jwks_client = PyJWKClient(jwks_url)
    signing_key = jwks_client.get_signing_key_from_jwt(access_token)

    decoded = jwt.decode(
        access_token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        options={"require": ["exp", "iat", "iss", "token_use"]},
    )

    if decoded.get("token_use") != "access":
        raise jwt.InvalidTokenError("Token is not an access token")

    return decoded["sub"]


@app.entrypoint
def runtime_memory_agent(payload, context):
    """
    Main entry point for the memory-enabled agent

    Args:
        payload: The input payload containing user data
        context: The runtime context object containing session information
    """
    global agent

    # Log both payload and context info
    logger.info(f"Received payload: {payload}")
    logger.info(f"Context: {context}")
    logger.info(f"Context Auth: {context.request_headers.get('Authorization')}")
    logger.info(f"User Sub: {get_user_sub(context.request_headers.get('Authorization'), REGION, COGNITO_USER_POOL)}")
    logger.info(f"Context session_id: {context.session_id}")

    # Extract and validate required values
    user_input = payload.get("prompt")
    actor_id = get_user_sub(context.request_headers.get("Authorization"), REGION, COGNITO_USER_POOL)
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
        logger.info("Using existing agent instance")
        # Update the session ID in case it changed
        if agent.state.get("session_id") != session_id:
            logger.info(f"Updating session ID to {session_id}")
            agent.state.set("session_id", session_id)
        if agent.state.get("actor_id") != actor_id:
            logger.info(f"Updating actor ID to {actor_id}")
            agent.state.set("actor_id", actor_id)

    # Invoke the agent with the user's input
    logger.info(f"Invoking agent with input: {user_input}")
    response = agent(user_input)
    response_text = response.message["content"][0]["text"]
    logger.info(f"✅ Agent response: {response_text[:50]}...")

    return response_text


if __name__ == "__main__":
    logger.info("Starting AgentCore application")
    app.run()
