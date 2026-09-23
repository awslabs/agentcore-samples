#!/usr/bin/env python3
"""
AgentCore Memory Manager

Provides short-term memory capabilities for AgentCore agents using Amazon Bedrock AgentCore Memory.
Handles conversation context storage and retrieval for maintaining conversation flow.
"""

# ============================================================================
# IMPORTS
# ============================================================================

import os
import yaml
import uuid
from typing import List, Optional, Dict, Any
from agent_shared import mylogger

logger = mylogger.get_logger()

try:
    from bedrock_agentcore.memory import MemoryClient
except ImportError:
    logger.warning("bedrock-agentcore not installed. Memory functionality will be disabled.")
    MemoryClient = None

# ============================================================================
# CLASSES
# ============================================================================


class MemoryManager:
    """Manages short-term memory for AgentCore agents"""

    def __init__(self, config_path: str = None):
        """Initialize memory manager with configuration"""
        self.config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "config",
        )
        self.config = self._load_config()
        self.memory_client = None
        self.memory_id = None
        self.session_id = None
        self._initialize_memory()

    def _load_config(self) -> Dict[str, Any]:
        """Load AgentCore configuration"""
        try:
            config_file = os.path.join(self.config_path, "agentcore-config.yaml")
            with open(config_file, "r") as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning(f"Config file not found at {config_file}. Memory functionality may be limited.")
            return {}
        except yaml.YAMLError as e:
            logger.warning(f"Error parsing config file: {e}. Memory functionality may be limited.")
            return {}

    def _initialize_memory(self):
        """Initialize memory client and resources"""
        if MemoryClient is None:
            logger.info("Memory client not available. Skipping memory initialization.")
            return

        try:
            region = self.config.get("aws", {}).get("region", "us-east-1")
            self.memory_client = MemoryClient(region_name=region)

            # Try to find existing memory or create new one
            self._setup_memory_resource()

        except Exception as e:
            logger.error(f"Failed to initialize memory client: {e}")
            self.memory_client = None

    def _setup_memory_resource(self):
        """Setup memory resource for the agent"""
        if not self.memory_client:
            return

        try:
            # Look for existing memory with our naming convention
            memories = list(self.memory_client.list_memories())
            agent_memory = None

            for memory in memories:
                if memory.get("name") == "AgentCoreConversationMemory":
                    agent_memory = memory
                    break

            if agent_memory:
                self.memory_id = agent_memory.get("id")
                logger.info(f"Using existing memory resource: {self.memory_id}")
            else:
                # Create new short-term memory
                memory = self.memory_client.create_memory(
                    name="AgentCoreConversationMemory",
                    description="Short-term memory for AgentCore agent conversations",
                )
                self.memory_id = memory.get("id")
                logger.info(f"Created new memory resource: {self.memory_id}")

        except Exception as e:
            logger.error(f"Failed to setup memory resource: {e}")
            self.memory_client = None

    def start_session(self, session_id: str = None) -> str:
        """Start a new conversation session"""
        self.session_id = session_id or f"session_{uuid.uuid4().hex[:8]}"
        return self.session_id

    def get_session_id(self) -> Optional[str]:
        """Get current session ID"""
        return self.session_id

    def store_conversation_turn(
        self,
        user_message: str,
        assistant_response: str,
        actor_id: str = "user",
        tool_calls: List[str] = None,
    ) -> bool:
        """Store a conversation turn in short-term memory"""
        if not self.memory_client or not self.memory_id or not self.session_id:
            return False

        try:
            # Build messages list for this turn
            messages = [(user_message, "USER"), (assistant_response, "ASSISTANT")]

            # Add tool calls if any
            if tool_calls:
                for tool_call in tool_calls:
                    messages.insert(-1, (tool_call, "TOOL"))

            # Store in memory
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=self.session_id,
                messages=messages,
            )

            return True

        except Exception as e:
            logger.error(f"Failed to store conversation turn: {e}")
            return False

    def get_conversation_context(self, actor_id: str = "user", max_results: int = 10) -> List[Dict[str, Any]]:
        """Retrieve conversation context from short-term memory"""
        if not self.memory_client or not self.memory_id or not self.session_id:
            return []

        try:
            conversations = self.memory_client.list_events(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=self.session_id,
                max_results=max_results,
            )

            return conversations if conversations else []

        except Exception as e:
            logger.error(f"Failed to retrieve conversation context: {e}")
            return []

    def format_context_for_agent(self, actor_id: str = "user", max_results: int = 5) -> str:
        """Format conversation context as a string for agent input"""
        conversations = self.get_conversation_context(actor_id, max_results)

        if not conversations:
            return ""

        context_parts = ["Previous conversation context:"]

        try:
            for event in conversations:
                messages = event.get("messages", [])
                timestamp = event.get("createdAt", "Unknown time")

                context_parts.append(f"\n[{timestamp}]")
                for message in messages:
                    content = message.get("content", "")
                    role = message.get("role", "UNKNOWN")

                    if role == "USER":
                        context_parts.append(f"User: {content}")
                    elif role == "ASSISTANT":
                        context_parts.append(f"Assistant: {content}")
                    elif role == "TOOL":
                        context_parts.append(f"Tool: {content}")

        except Exception as e:
            logger.error(f"Error formatting context: {e}")
            return ""

        return "\n".join(context_parts)

    def clear_session(self):
        """Clear current session context"""
        self.session_id = None

    def is_memory_available(self) -> bool:
        """Check if memory functionality is available"""
        return self.memory_client is not None and self.memory_id is not None

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory usage statistics"""
        if not self.is_memory_available():
            return {"status": "unavailable", "reason": "Memory client not initialized"}

        try:
            # Get recent conversation count
            conversations = self.get_conversation_context(max_results=100)

            return {
                "status": "available",
                "memory_id": self.memory_id,
                "session_id": self.session_id,
                "conversation_count": len(conversations),
                "region": self.config.get("aws", {}).get("region", "unknown"),
            }

        except Exception as e:
            return {"status": "error", "error": str(e)}
