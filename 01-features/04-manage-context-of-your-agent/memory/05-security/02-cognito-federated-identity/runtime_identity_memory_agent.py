import os
import jwt
import ast
import boto3
import logging

from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

# Configure detailed logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("runtime-memory-agent")

# Initialize the agent core app
app = BedrockAgentCoreApp()

MODEL_ID = os.getenv("MODEL_ID")
MEMORY_ID = os.getenv("MEMORY_ID")
COGNITO_USER_POOL = os.getenv("COGNITO_USER_POOL")
IDENTITY_POOL_ID = os.getenv("IDENTITY_POOL_ID")
REGION = os.getenv("AWS_REGION")

# Global agent instance - will be initialized with first request
agent = None
memory_session_manager = None
actor_id = None


def get_aws_credentials_for_identity(identity_pool_id, id_token, region, user_pool_id):
    """
    Get temporary AWS credentials for a Cognito identity using a User Pool ID token
    """
    identity_client = boto3.client("cognito-identity", region_name=region)

    # Get ID from identity pool
    get_id_response = identity_client.get_id(
        IdentityPoolId=identity_pool_id,
        Logins={f"cognito-idp.{region}.amazonaws.com/{user_pool_id}": id_token},
    )
    identity_id = get_id_response["IdentityId"]

    # Get credentials for the identity
    get_credentials_response = identity_client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins={f"cognito-idp.{region}.amazonaws.com/{user_pool_id}": id_token},
    )

    # Return the temporary credentials
    credentials = get_credentials_response["Credentials"]
    return {
        "access_key_id": credentials["AccessKeyId"],
        "secret_key": credentials["SecretKey"],
        "session_token": credentials["SessionToken"],
        "expiration": credentials["Expiration"],
        "identity_id": identity_id,
    }


class MemoryHookProvider(HookProvider):
    """Custom hook provider to integrate with Bedrock Memory"""

    def __init__(self):
        logger.info("Initializing MemoryHookProvider")
        # Use global memory_client that may have federated credentials
        self.memory_session_manager = memory_session_manager

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        logger.info("Agent initialization hook triggered")

        actor_id = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")

        logger.info(f"State values - actor_id: {actor_id}, session_id: {session_id}")

        if not all([actor_id, session_id]):
            logger.warning("Missing required state values")
            return

        try:
            # Check if the session exists
            logger.info(f"Checking if session {session_id} exists...")
            session_exists = False
            try:
                events = self.memory_session_manager.list_events(
                    actor_id=actor_id, session_id=session_id, max_results=1
                )
                session_exists = len(events) > 0
                logger.info(f"Session exists: {session_exists}")
            except Exception as e:
                logger.warning(f"Error checking session existence: {e}")
                session_exists = False

            if not session_exists:
                logger.info(f"No existing conversation found for session {session_id}")
                return

            # Load conversation history
            logger.info(f"Loading conversation history for existing session {session_id}")
            recent_turns = self.memory_session_manager.get_last_k_turns(actor_id=actor_id, session_id=session_id, k=5)

            if recent_turns:
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns from memory")

                # Add messages to agent's conversation history
                for turn in reversed(recent_turns):
                    for message in turn:
                        role = message["role"].lower()  # 'user' or 'assistant'
                        parsed = ast.literal_eval(message["content"]["text"])
                        content = parsed[0]["text"]

                        # Add to agent's message history
                        event.agent.messages.append({"role": role, "content": [{"text": content}]})
                        logger.info(f"Added {role} message to history: {content[:50]}...")

                logger.info(f"✅ Added {len(event.agent.messages)} messages to conversation history")
            else:
                logger.info("No recent turns found for this session")

        except Exception as e:
            logger.error(f"❌ Memory load error: {e}", exc_info=True)

    def on_message_added(self, event: MessageAddedEvent):
        """Store messages in memory"""
        logger.info("💬 Message added - storing in memory")

        actor_id = event.agent.state.get("actor_id")
        session_id = event.agent.state.get("session_id")

        if not all([actor_id, session_id]):
            logger.warning("Missing required state values")
            return

        try:
            messages = event.agent.messages
            last_message = messages[-1]
            message_content = str(last_message.get("content", ""))
            if last_message["role"] == "user":
                message_role = MessageRole.USER
            elif last_message["role"] == "assistant":
                message_role = MessageRole.ASSISTANT

            self.memory_session_manager.add_turns(
                actor_id=actor_id,
                session_id=session_id,
                messages=[ConversationalMessage(message_content, message_role)],
            )
            logger.info("✅ Message stored")

        except Exception as e:
            logger.error(f"❌ Error storing message: {e}")

    def register_hooks(self, registry: HookRegistry):
        logger.info("Registering memory hooks")
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)


def initialize_agent(actor_id, session_id):
    """Initialize the agent for first use"""
    global agent

    logger.info(
        f"Initializing agent for actor_id={actor_id}, session_id={session_id}"
    )  # codeql[py/clear-text-logging-sensitive-data]

    # Create model and memory hook
    logger.info(f"Creating model with ID: {MODEL_ID}")
    model = BedrockModel(model_id=MODEL_ID)
    logger.info(f"Creating memory hook with region: {REGION}")
    memory_hook = MemoryHookProvider()

    # Create agent with proper initial state
    logger.info("Creating agent with memory hook")
    agent = Agent(
        model=model,
        hooks=[memory_hook],
        system_prompt="You're a helpful agent. You can remember previous interactions within the same session. Be friendly and concise in your responses.",
        state={"actor_id": actor_id, "session_id": session_id},
    )
    logger.info(f"✅ Agent initialized with state: {agent.state.get()}")


def log_cognito_sub_from_token(id_token):
    """Log the sub value from the Cognito ID token"""
    try:
        # Decode without verification (just for logging the sub claim, not for auth)
        decoded = jwt.decode(  # nosec B105
            id_token,
            options={
                "verify_signature": False,  # nosec: intentional — read-only claim inspection
                "verify_aud": False,
                "verify_exp": False,
            },
        )
        logger.info(f"ID Token sub claim: {decoded.get('sub')}")
        return decoded.get("sub")
    except Exception as e:
        logger.error(f"Error decoding token: {e}")
        return None


@app.entrypoint
def runtime_memory_agent(payload, context):
    """
    Main entry point for the memory-enabled agent with identity federation

    Args:
        payload: The input payload containing user data
        context: The runtime context object containing session information
    """
    global agent, memory_session_manager

    # Log both payload and context info
    logger.info(f"Received payload: {payload}")
    logger.info(f"Context: {context}")
    logger.info(f"Context Auth: {context.request_headers.get('Authorization')}")

    # Extract and validate required values
    user_input = payload.get("prompt")
    id_token = payload.get("id_token")  # Get the ID token from payload
    auth_header = context.request_headers.get("Authorization")  # noqa: F841
    session_id = context.session_id

    # Validate required fields
    if user_input is None:
        error_msg = "❌ ERROR: Missing 'prompt' field in payload"
        logger.error(error_msg)
        return error_msg

    # Set up federated identity if ID token is provided
    if id_token and IDENTITY_POOL_ID:
        logger.info("ID token provided - setting up federated identity")

        # Get AWS credentials using the ID token
        user_credentials = get_aws_credentials_for_identity(
            identity_pool_id=IDENTITY_POOL_ID,
            id_token=id_token,
            region=REGION,
            user_pool_id=COGNITO_USER_POOL,
        )

        # Set up actor_id
        logger.info(
            f"Identity Credentials: {user_credentials['identity_id']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        actor_id = user_credentials["identity_id"]

        # Set up boto3 session with federated credentials
        session = boto3.Session(
            aws_access_key_id=user_credentials["access_key_id"],
            aws_secret_access_key=user_credentials["secret_key"],
            aws_session_token=user_credentials["session_token"],
            region_name=REGION,
        )

        log_cognito_sub_from_token(id_token)

        # Create memory client with federated credentials
        memory_session_manager = MemorySessionManager(memory_id=MEMORY_ID, region_name=REGION, boto3_session=session)
        logger.info("✅ Successfully configured federated credentials for memory operations")

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
            logger.info(f"Updating actor ID to {actor_id}")  # codeql[py/clear-text-logging-sensitive-data]
            agent.state.set("actor_id", actor_id)

    # Invoke the agent with the user's input
    logger.info(f"Invoking agent with input: {user_input}")
    response = agent(user_input)
    response_text = response.message["content"][0]["text"]
    logger.info(f"✅ Agent response: {response_text[:50]}…")

    return response_text


if __name__ == "__main__":
    logger.info("Starting AgentCore application")
    app.run()
