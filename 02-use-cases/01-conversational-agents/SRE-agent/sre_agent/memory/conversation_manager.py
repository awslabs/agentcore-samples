import logging
from datetime import datetime
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from .client import SREMemoryClient

# Configure logging with basicConfig
logging.basicConfig(
    level=logging.INFO,  # Set the log level to INFO
    # Define log message format
    format="%(asctime)s,p%(process)s,{%(filename)s:%(lineno)d},%(levelname)s,%(message)s",
)

logger = logging.getLogger(__name__)


class ConversationMessage(BaseModel):
    """Conversation message model for automatic memory storage."""

    content: str = Field(description="The message content")
    role: str = Field(description="Message role: USER, ASSISTANT, or TOOL")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When this message was created"
    )
    agent_name: Optional[str] = Field(
        default=None,
        description="Name of the agent that generated this message (if applicable)",
    )
    session_id: Optional[str] = Field(
        default=None, description="Session identifier for grouping messages"
    )


class ConversationMemoryManager:
    """Manager for automatic conversation tracking through AgentCore memory."""

    def __init__(self, memory_client: SREMemoryClient):
        self.memory_client = memory_client
        logger.info("Initialized ConversationMemoryManager")

    def store_conversation_message(
        self,
        content: str,
        role: str,
        user_id: str,
        session_id: str,
        agent_name: Optional[str] = None,
    ) -> bool:
        """
        Store a conversation message in memory using create_event.

        Args:
            content: The message content
            role: USER, ASSISTANT, or TOOL
            user_id: User ID to use as actor_id for create_event
            session_id: Session identifier (required)
            agent_name: Name of the agent (if applicable)

        Returns:
            bool: Success status
        """
        try:
            if not user_id:
                raise ValueError("user_id is required for conversation message storage")
            if not session_id:
                raise ValueError(
                    "session_id is required for conversation message storage"
                )

            # Create conversation message model (for validation)
            ConversationMessage(
                content=content, role=role, agent_name=agent_name, session_id=session_id
            )

            logger.info(
                f"Storing conversation message: role={role}, user_id={user_id}, session_id={session_id}, agent={agent_name}, content_length={len(content)}"
            )

            # Format message as tuple for AgentCore memory
            message_tuple = (content, role)

            # Use AgentCore's create_event with user_id as actor_id
            result = self.memory_client.client.create_event(
                memory_id=self.memory_client.memory_id,
                actor_id=user_id,  # Use user_id as actor_id as specified
                session_id=session_id,  # Use provided session_id
                messages=[message_tuple],  # AgentCore expects list of tuples
            )

            event_id = result.get("eventId", "unknown")
            logger.info(
                f"Successfully stored conversation message (event_id: {event_id})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to store conversation message: {e}", exc_info=True)
            return False

    def store_conversation_batch(
        self,
        messages: List[Tuple[str, str]],
        user_id: str,
        session_id: str,
        agent_name: Optional[str] = None,
    ) -> bool:
        """
        Store multiple conversation messages in a single create_event call.

        Args:
            messages: List of (content, role) tuples
            user_id: User ID to use as actor_id
            session_id: Session identifier (required)
            agent_name: Name of the agent (if applicable)

        Returns:
            bool: Success status
        """
        try:
            if not user_id:
                raise ValueError("user_id is required for conversation batch storage")
            if not session_id:
                raise ValueError(
                    "session_id is required for conversation batch storage"
                )
            if not messages:
                logger.warning("No messages provided to store_conversation_batch")
                return True

            logger.info(
                f"Storing conversation batch: {len(messages)} messages, user_id={user_id}, session_id={session_id}, agent={agent_name}"
            )

            # Truncate messages that exceed the maximum content length limit
            from ..constants import SREConstants

            max_content_length = SREConstants.memory.max_content_length
            truncated_messages = []

            for content, role in messages:
                if len(content) > max_content_length:
                    # Truncate content and add warning message
                    truncated_content = (
                        content[: max_content_length - 100]
                        + "\n\n[TRUNCATED: Content exceeded maximum length limit]"
                    )
                    truncated_messages.append((truncated_content, role))
                    logger.warning(
                        f"Truncated message content from {len(content)} to {len(truncated_content)} characters for user_id={user_id}, session_id={session_id}"
                    )
                else:
                    truncated_messages.append((content, role))

            # Use AgentCore's create_event with batch of messages
            result = self.memory_client.client.create_event(
                memory_id=self.memory_client.memory_id,
                actor_id=user_id,  # Use user_id as actor_id as specified
                session_id=session_id,  # Use provided session_id
                messages=truncated_messages,  # AgentCore expects list of tuples
            )

            event_id = result.get("eventId", "unknown")
            logger.info(
                f"Successfully stored conversation batch of {len(messages)} messages (event_id: {event_id})"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to store conversation batch: {e}", exc_info=True)
            return False


def create_conversation_memory_manager(
    memory_client: SREMemoryClient,
) -> ConversationMemoryManager:
    """Create a conversation memory manager instance."""
    return ConversationMemoryManager(memory_client)
