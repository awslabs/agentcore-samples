"""
Knowledge Base RAG Agent - Memory Manager

This module provides memory management using AgentCore Memory service.
Based on: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-getting-started.html
"""

import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Manages conversation memory using AgentCore Memory service.

    Supports short-term memory (conversation events) which AgentCore
    automatically processes into long-term memories based on configured
    extraction strategies (semantic, user preference, summarization).
    """

    def __init__(self, memory_id: Optional[str] = None, region: str = "us-east-1"):
        """
        Initialize the memory manager.

        Args:
            memory_id: The AgentCore Memory ID
            region: AWS region
        """
        self.memory_id = memory_id or os.environ.get("MEMORY_ID")
        self.region = region
        self._client = None

        if self.memory_id:
            logger.info(f"Memory enabled with ID: {self.memory_id[:30]}...")
        else:
            logger.info("No memory ID provided - memory features disabled")

    @property
    def client(self):
        """Lazy initialization of the AgentCore data plane client."""
        if self._client is None:
            # Use bedrock-agentcore for data plane operations (create_event, etc.)
            self._client = boto3.client("bedrock-agentcore", region_name=self.region)
        return self._client

    def store_interaction(self, actor_id: str, session_id: str, user_message: str, assistant_message: str) -> bool:
        """
        Store a conversation interaction in memory.

        Args:
            actor_id: The user/actor identifier
            session_id: The session identifier
            user_message: The user's message
            assistant_message: The assistant's response

        Returns:
            True if successful, False otherwise
        """
        if not self.memory_id:
            return False

        try:
            timestamp = datetime.now(timezone.utc)

            # Create event with both conversation turns
            # Based on: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/aws-sdk-memory.html
            self.client.create_event(
                memoryId=self.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=timestamp,
                payload=[
                    {
                        "conversational": {
                            "content": {"text": user_message},
                            "role": "USER",
                        }
                    },
                    {
                        "conversational": {
                            "content": {"text": assistant_message},
                            "role": "ASSISTANT",
                        }
                    },
                ],
            )

            logger.debug(f"Stored interaction for session {session_id}")
            return True

        except ClientError as e:
            logger.error(f"Error storing interaction: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error storing interaction: {e}")
            return False

    def get_conversation_history(self, actor_id: str, session_id: str, max_results: int = 10) -> List[dict]:
        """
        Retrieve conversation history from short-term memory.

        Note: AgentCore Memory doesn't have a direct list_events API in the
        data plane. Conversation history is managed internally by AgentCore
        and used for memory extraction. For now, return empty list.

        Args:
            actor_id: The user/actor identifier
            session_id: The session identifier
            max_results: Maximum number of events to retrieve

        Returns:
            List of conversation events (empty for now)
        """
        # AgentCore manages conversation history internally
        # The events we store are processed by extraction strategies
        return []

    def retrieve_memories(self, actor_id: str, query: str, max_results: int = 5) -> List[str]:
        """
        Retrieve relevant long-term memories based on a query.

        Note: Memory retrieval requires the get_memory_record API which
        retrieves specific records. For semantic search across memories,
        AgentCore uses the configured extraction strategies automatically.

        Args:
            actor_id: The user/actor identifier
            query: The search query
            max_results: Maximum number of memories to retrieve

        Returns:
            List of relevant memory strings (empty for now - AgentCore handles this internally)
        """
        # AgentCore Memory extraction strategies handle this automatically
        # The Runtime has access to memories through its configuration
        return []
