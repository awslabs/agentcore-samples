"""
ShortTermMemoryHook for AgentCore Memory Integration

This module provides a reusable hook provider that automatically manages
short-term memory for Strands agents using Amazon Bedrock AgentCore Memory.

Features:
- Automatic conversation history loading on agent initialization
- Automatic message persistence on message addition
- Support for application context injection
- Error handling and logging
"""

import logging
from typing import Optional, List, Dict, Any

from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from bedrock_agentcore.memory import MemoryClient

# Configure logger
logger = logging.getLogger(__name__)


class ShortTermMemoryHook(HookProvider):
    """
    Automatic memory management hook for Strands agents.

    This hook provider integrates Amazon Bedrock AgentCore Memory with Strands agents,
    providing seamless conversation history loading and persistence without manual intervention.

    Key features:
    - Loads recent conversation turns on agent initialization
    - Automatically saves messages after each agent invocation
    - Extracts application-specific information for context injection
    - Handles memory lookups and error cases gracefully

    Args:
        memory_client: MemoryClient instance for AgentCore Memory operations
        memory_id: ID of the AgentCore Memory resource
        context_keywords: Optional list of keywords to identify application context
        max_context_turns: Maximum number of recent turns to load (default: 5)
        branch_name: Memory branch name to use (default: "main")

    Example:
        >>> memory_client = MemoryClient(region_name='us-west-2')
        >>> memory_hook = ShortTermMemoryHook(memory_client, memory_id="xyz-123")
        >>> agent = Agent(
        ...     hooks=[memory_hook],
        ...     model="global.anthropic.claude-sonnet-4-20250514-v1:0",
        ...     state={"actor_id": "user-123", "session_id": "session-456"}
        ... )
    """

    def __init__(
        self,
        memory_client: MemoryClient,
        memory_id: str,
        context_keywords: Optional[List[str]] = None,
        max_context_turns: int = 5,
        branch_name: str = "main",
    ):
        """Initialize the ShortTermMemoryHook."""
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.max_context_turns = max_context_turns
        self.branch_name = branch_name

        # Default keywords for identifying application context
        self.context_keywords = context_keywords or [
            "Stack Name:",
            "EC2 Instance:",
            "Database:",
            "Application:",
            "Service:",
            "Configuration:",
            "Error:",
            "Status:",
            "Memory:",
            "CPU:",
        ]

        logger.debug(
            f"ShortTermMemoryHook initialized with memory_id={memory_id}, max_context_turns={max_context_turns}"
        )

    def on_agent_initialized(self, event: AgentInitializedEvent) -> None:
        """
        Load recent conversation history when agent initializes.

        This method is called when a Strands agent is initialized. It retrieves
        recent conversation turns from memory and injects them into the agent's
        system prompt as context, prioritizing application-specific information.

        Args:
            event: AgentInitializedEvent containing the agent instance
        """
        try:
            # Extract actor and session identifiers from agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning(
                    "Cannot load memory: Missing actor_id or session_id in agent state. "
                    f"actor_id={actor_id}, session_id={session_id}"
                )
                return

            logger.debug(f"Loading memory for actor_id={actor_id}, session_id={session_id}")

            # Retrieve recent conversation turns from memory
            recent_turns = self.memory_client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=self.max_context_turns,
                branch_name=self.branch_name,
            )

            if recent_turns:
                context = self._build_context_from_turns(recent_turns)
                event.agent.system_prompt += f"\n\n{context}\n\nUse this information for additional background context."
                logger.info(
                    f"✅ Loaded {len(recent_turns)} conversation turns into agent context (actor_id={actor_id})"
                )
            else:
                logger.info(f"No previous conversation history found for actor_id={actor_id}")

        except Exception as e:
            logger.error(
                f"Failed to load conversation history: {type(e).__name__}: {str(e)}",
                exc_info=True,
            )

    def on_message_added(self, event: MessageAddedEvent) -> None:
        """
        Automatically persist messages to memory.

        This method is called whenever a new message is added to the agent's
        message history (user input or agent response). The message is persisted
        to AgentCore Memory for later retrieval.

        Args:
            event: MessageAddedEvent containing the agent and new message
        """
        try:
            # Extract agent state
            actor_id = event.agent.state.get("actor_id")
            session_id = event.agent.state.get("session_id")

            if not actor_id or not session_id:
                logger.warning("Cannot save message: Missing actor_id or session_id in agent state")
                return

            # Get the most recent message
            messages = event.agent.messages
            if not messages:
                logger.warning("No messages found to persist")
                return

            latest_message = messages[-1]
            message_role = latest_message.get("role", "unknown")

            # Extract text content from message
            message_content = latest_message.get("content", [])
            if isinstance(message_content, list) and message_content:
                message_text = message_content[0].get("text", "")
            else:
                message_text = str(message_content)

            # Skip empty messages (don't persist to memory if text is empty)
            if not message_text or not message_text.strip():
                logger.debug(f"Skipping empty message (role={message_role}) - no content to persist")
                return

            # Save to memory
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(message_text, message_role)],
            )

            logger.debug(
                f"✅ Persisted message (role={message_role}, length={len(message_text)}) for actor_id={actor_id}"
            )

        except Exception as e:
            logger.error(
                f"Failed to persist message to memory: {type(e).__name__}: {str(e)}",
                exc_info=True,
            )

    def register_hooks(self, registry: HookRegistry) -> None:
        """
        Register hooks with the Strands agent hook registry.

        This method is called by the Strands framework to register this hook provider's
        callbacks with the agent's event system.

        Args:
            registry: HookRegistry instance to register callbacks with
        """
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        logger.debug("ShortTermMemoryHook callbacks registered with HookRegistry")

    def _build_context_from_turns(self, turns: List[List[Dict[str, Any]]]) -> str:
        """
        Build context string from conversation turns.

        Extracts application-specific information and recent conversation history,
        prioritizing structured information (application details) for better context.

        Args:
            turns: List of conversation turns, each containing a list of messages

        Returns:
            Formatted context string for injection into system prompt
        """
        context_messages = []
        application_info = []

        # Process each turn and extract information
        for turn in turns:
            for message in turn:
                role = message.get("role", "").lower()
                content = message.get("content", {})

                # Handle different content formats
                if isinstance(content, dict):
                    text = content.get("text", "")
                elif isinstance(content, str):
                    text = content
                else:
                    text = str(content)

                # Extract application information based on keywords
                is_application_info = any(keyword in text for keyword in self.context_keywords)

                if is_application_info and role == "assistant":
                    application_info.append(text)
                else:
                    context_messages.append(f"{role.title()}: {text}")

        # Build final context with application info prioritized
        context_parts = []

        if application_info:
            context_parts.append("APPLICATION INFORMATION:")
            context_parts.extend(application_info)

        if context_messages:
            if application_info:
                context_parts.append("\nRECENT CONVERSATION:")
            # Include only recent conversation messages (last 6)
            context_parts.extend(context_messages[-6:])

        return "\n".join(context_parts)
