# import the memory client
import logging
from typing import Dict, List
from bedrock_agentcore.memory import MemoryClient
from strands.hooks import (
    AgentInitializedEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
    AfterInvocationEvent,
)

# Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()


# Retrieval configuration class
class RetrievalConfig:
    """Configuration for memory retrieval"""

    def __init__(self, top_k: int = 3, relevance_score: float = 0.2):
        self.top_k = top_k
        self.relevance_score = relevance_score


# Create monitoring memory hooks with long-term memory support
class MonitoringMemoryHooks(HookProvider):
    """Memory hooks for monitoring agent - Enhanced with long-term memory"""

    def __init__(self, memory_id: str, client: MemoryClient, actor_id: str, session_id: str):
        self.memory_id = memory_id
        self.client = client
        self.actor_id = actor_id
        self.session_id = session_id

        # Define retrieval configuration for different memory namespaces
        # These match the CloudFormation memory strategy namespaces
        self.retrieval_config = {
            "/technical-issues/{actorId}": RetrievalConfig(top_k=3, relevance_score=0.3),
            "/knowledge/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.2),
        }

    def retrieve_monitoring_context(self, event: MessageAddedEvent):
        """Retrieve long-term monitoring context before processing queries"""
        messages = event.agent.messages
        if messages[-1]["role"] == "user" and "toolResult" not in messages[-1]["content"][0]:
            user_query = messages[-1]["content"][0]["text"]

            try:
                # Search across different long-term memory namespaces
                relevant_memories = []

                for namespace_template, config in self.retrieval_config.items():
                    # Resolve namespace template with actual actor ID
                    resolved_namespace = namespace_template.format(actorId=self.actor_id)

                    # Retrieve memories from this namespace
                    memories = self.client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=resolved_namespace,
                        query=user_query,
                        top_k=config.top_k,
                    )

                    # Filter by relevance score
                    filtered_memories = [
                        memory for memory in memories if memory.get("score", 0) >= config.relevance_score
                    ]

                    relevant_memories.extend(filtered_memories)
                    logger.info(f"Found {len(filtered_memories)} relevant memories in {resolved_namespace}")

                # Inject context into agent's system prompt if memories found
                if relevant_memories:
                    context_text = self._format_context(relevant_memories)
                    original_prompt = event.agent.system_prompt
                    enhanced_prompt = f"{original_prompt}\n\n<memory-context>\n{context_text}\n</memory-context>\n"

                    event.agent.system_prompt = enhanced_prompt
                    logger.info(f"✅ Injected {len(relevant_memories)} long-term memories into agent context")

            except Exception as e:
                logger.error(f"Failed to retrieve monitoring context: {e}")

    def _format_context(self, memories: List[Dict]) -> str:
        """Format retrieved long-term memories for agent context"""
        context_lines = []
        for i, memory in enumerate(memories[:5], 1):  # Limit to top 5
            content = memory.get("content", {})
            if isinstance(content, dict):
                text = content.get("text", "No content available").strip()
            else:
                text = str(content)
            score = memory.get("score", 0)
            context_lines.append(f"{i}. (Score: {score:.2f}) {text[:200]}...")

        return "\n".join(context_lines)

    def save_monitoring_interaction(self, event: AfterInvocationEvent):
        """Save monitoring interaction to short-term conversational memory"""
        try:
            messages = event.agent.messages
            if len(messages) >= 2 and messages[-1]["role"] == "assistant":
                # Get last user query and agent response
                user_query = None
                agent_response = None

                for msg in reversed(messages):
                    if msg["role"] == "assistant" and not agent_response:
                        agent_response = msg["content"][0]["text"]
                    elif msg["role"] == "user" and not user_query and "toolResult" not in msg["content"][0]:
                        user_query = msg["content"][0]["text"]
                        break

                if user_query and agent_response:
                    # Save to short-term conversational memory using create_event
                    result = self.client.create_event(
                        memory_id=self.memory_id,
                        actor_id=self.actor_id,
                        session_id=self.session_id,
                        messages=[(user_query, "USER"), (agent_response, "ASSISTANT")],
                    )
                    event_id = result.get("eventId", "unknown")
                    logger.info(f"✅ Saved monitoring interaction to short-term memory - Event ID: {event_id}")

        except Exception as e:
            logger.error(f"Failed to save monitoring interaction: {e}")

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts"""
        try:
            # Load the last 5 conversation turns from memory
            recent_turns = self.client.get_last_k_turns(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                k=5,
            )

            if recent_turns:
                # Format conversation history for context
                context_messages = []
                for turn in recent_turns:
                    for message in turn:
                        role = message["role"]
                        content = message["content"]["text"]
                        context_messages.append(f"{role}: {content}")

                context = "\n".join(context_messages)
                # Add context to agent's system prompt.
                event.agent.system_prompt += f"\n\n<recent-conversation>:\n{context}\n</recent-conversation>\n"
                logger.info(f"✅ Loaded {len(recent_turns)} conversation turns")

        except Exception as e:
            logger.error(f"Memory load error: {e}")

    def register_hooks(self, registry: HookRegistry) -> None:
        """
        Register monitoring memory hooks

        Memory Architecture:
        - SHORT-TERM: create_event() stores conversational turns (expires after 60 days)
        - LONG-TERM: retrieve_memories() searches semantic/custom strategy namespaces
          - /technical-issues/{actorId}: CustomMemoryStrategy with extraction prompts
          - /knowledge/{actorId}: SemanticMemoryStrategy for general facts

        The CustomMemoryStrategy automatically extracts monitoring facts from conversations
        using the extraction prompts defined in CloudFormation.
        """
        registry.add_callback(MessageAddedEvent, self.retrieve_monitoring_context)
        registry.add_callback(AfterInvocationEvent, self.save_monitoring_interaction)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        logger.info("✅ Monitoring memory hooks registered with long-term memory support")
