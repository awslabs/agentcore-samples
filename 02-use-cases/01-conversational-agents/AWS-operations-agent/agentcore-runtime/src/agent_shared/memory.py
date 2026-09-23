# ============================================================================
# IMPORTS
# ============================================================================

from datetime import datetime
from .config import load_configs

from . import mylogger

logger = mylogger.get_logger()

# Global variables for memory state
_memory_initialized = False
_memory_client = None
_current_session_id = None

# ============================================================================
# MEMORY SETUP
# ============================================================================


def setup_memory():
    """
    Set up AgentCore memory client.

    Returns:
        bool: True if successful, False if not available
    """
    global _memory_initialized, _memory_client

    if _memory_initialized:
        return True

    try:
        # Import AgentCore memory client
        from bedrock_agentcore.memory import MemoryClient

        # Load configuration
        agentcore_config, _ = load_configs()

        # Get memory configuration
        agentcore_config.get("memory", {})
        region = agentcore_config.get("aws", {}).get("region", "us-east-1")

        # Create memory client
        _memory_client = MemoryClient(region_name=region)
        _memory_initialized = True

        logger.info("✅ AgentCore Memory client initialized")
        return True

    except ImportError:
        logger.warning("⚠️ bedrock_agentcore.memory not available - memory disabled")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to initialize memory: {e}")
        return False


# ============================================================================
# CONTEXT RETRIEVAL
# ============================================================================


def get_conversation_context(session_id, actor_id, max_results=3):
    """
    Get previous conversation context using AgentCore Memory API.

    Args:
        session_id (str): Session ID for conversation
        actor_id (str): Actor ID (usually "user")
        max_results (int): Maximum number of previous turns to retrieve

    Returns:
        str: Conversation context or empty string if not available
    """
    global _memory_client, _current_session_id

    if not _memory_initialized or not _memory_client or not session_id:
        return ""

    try:
        # Set current session if different
        if _current_session_id != session_id:
            _current_session_id = session_id
            logger.info(f"📝 Started memory session: {session_id}")

        # Get memory ID from configuration
        agentcore_config, _ = load_configs()
        memory_config = agentcore_config.get("memory", {})
        memory_id = memory_config.get("id")

        if not memory_id:
            logger.info("📝 Memory ID not found in configuration - no context available")
            return ""

        # Get last few conversation turns using AgentCore Memory API
        turns = _memory_client.get_last_k_turns(
            memory_id=memory_id, actor_id=actor_id, session_id=session_id, k=max_results
        )

        if turns:
            # DEBUG: Log the raw memory data to understand what's causing validation errors
            logger.info(f"🔍 DEBUG: Raw memory turns structure: {len(turns)} turns")
            for i, turn in enumerate(turns):
                logger.info(f"🔍 DEBUG: Turn {i}: {len(turn)} messages")
                for j, message in enumerate(turn):
                    logger.info(
                        f"🔍 DEBUG: Turn {i} Message {j}: role={message.get('role')}, content_type={type(message.get('content'))}"
                    )
                    if isinstance(message.get("content"), dict):
                        logger.info(f"🔍 DEBUG: Content dict keys: {list(message.get('content', {}).keys())}")

            # Format context from memory turns
            context_parts = []
            for turn in turns:
                turn_messages = []
                for message in turn:
                    role = message.get("role", "unknown").upper()
                    # Handle content that might be a dict or string
                    content_raw = message.get("content", "")
                    if isinstance(content_raw, dict):
                        # If content is a dict, try to extract text from common fields
                        content = content_raw.get("text", str(content_raw))
                    else:
                        content = str(content_raw)

                    content = content.strip()
                    if content:
                        turn_messages.append(f"{role}: {content}")

                if turn_messages:
                    context_parts.append(" → ".join(turn_messages))

            if context_parts:
                context = "\n".join(context_parts)
                logger.info(f"📚 Retrieved {len(turns)} conversation turns from memory")
                return f"Previous conversation context:\n{context}\n"

        logger.info("📝 No previous context found in memory")
        return ""

    except Exception as e:
        logger.error(f"❌ Failed to get conversation context: {e}")
        return ""


# ============================================================================
# CONVERSATION STORAGE
# ============================================================================


def save_conversation(session_id, user_message, assistant_response, actor_id="user"):
    """
    Save conversation turn to memory using AgentCore Memory API.

    Args:
        session_id (str): Session ID for conversation
        user_message (str): User's message
        assistant_response (str): Assistant's response
        actor_id (str): Actor ID (usually "user")
    """
    global _memory_client

    if not _memory_initialized or not _memory_client or not session_id:
        logger.info("📝 Memory not available - conversation not saved")
        return

    try:
        # Get memory ID from configuration
        agentcore_config, _ = load_configs()
        memory_config = agentcore_config.get("memory", {})
        memory_id = memory_config.get("id")

        if not memory_id:
            logger.warning("⚠️ Memory ID not found in configuration - conversation not saved")
            return

        # Create event with conversation messages using AgentCore Memory API
        messages = [(user_message, "USER"), (assistant_response, "ASSISTANT")]

        result = _memory_client.create_event(
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id,
            messages=messages,
            event_timestamp=datetime.now(),
        )

        event_id = result.get("eventId", "unknown")
        logger.info(f"💾 Conversation saved to memory (event: {event_id}, session: {session_id})")

    except Exception as e:
        logger.error(f"❌ Failed to save conversation: {e}")


# ============================================================================
# ERROR HANDLING
# ============================================================================


def is_memory_available():
    """
    Check if memory functionality is available.

    Returns:
        bool: True if memory is available and initialized
    """
    return _memory_initialized and _memory_client is not None
