# Memory Tools for OpenAI Agents
# Based on the MonitoringMemoryHooks functionality

import logging
import time
from typing import Dict, Optional
from bedrock_agentcore.memory import MemoryClient
from agents import function_tool

logger = logging.getLogger(__name__)


class AgentMemoryTools:
    """Memory tools for OpenAI agents based on MonitoringMemoryHooks functionality"""

    def __init__(self, memory_id: str, client: MemoryClient, actor_id: str, session_id: str):
        # These are the variables required to create the memory for the bedrock agentcore
        # memory client, actor, session, namespaces, etc.
        self.memory_id = memory_id
        self.client = client
        self.actor_id = actor_id
        self.session_id = session_id
        self.namespaces = self._get_namespaces()

    def _get_namespaces(self) -> Dict:
        """Get namespace mapping for memory strategies.
        In this, we get the mapping strategies based on the
        different namespaces in memory
        """
        try:
            strategies = self.client.get_memory_strategies(self.memory_id)
            return {i["type"]: i["namespaces"][0] for i in strategies}
        except Exception as e:
            logger.error(f"Failed to get namespaces: {e}")
            return {}

    def create_memory_tools(self):
        """Create and return all memory-related tools for the agent"""

        # Capture self in closure for tool functions
        memory_id = self.memory_id
        client = self.client
        actor_id = self.actor_id
        session_id = self.session_id
        namespaces = self.namespaces

        @function_tool
        def retrieve_monitoring_context(query: str, context_type: Optional[str] = None, top_k: int = 3) -> str:
            """Retrieve monitoring context from memory using semantic search.

            Args:
                query: The search query to find relevant context
                context_type: Optional specific context type to search (e.g., 'UserPreference', 'SemanticMemory')
                top_k: Number of top results to return (default: 3)

            Returns:
                String containing the retrieved context
            """
            try:
                all_context = []

                # If specific context type is requested, search only that namespace
                if context_type and context_type in namespaces:
                    search_namespaces = {context_type: namespaces[context_type]}
                else:
                    # Search all namespaces
                    search_namespaces = namespaces

                for ctx_type, namespace in search_namespaces.items():
                    # We will retrieve memories for the given namespaces if any
                    memories = client.retrieve_memories(
                        memory_id=memory_id,
                        namespace=namespace.format(actorId=actor_id),
                        query=query,
                        top_k=top_k,
                    )

                    for memory in memories:
                        if isinstance(memory, dict):
                            content = memory.get("content", {})
                            if isinstance(content, dict):
                                text = content.get("text", "").strip()
                                if text:
                                    all_context.append(f"[{ctx_type.upper()}] {text}")

                if all_context:
                    context_text = "\n".join(all_context)
                    logger.info(f"Retrieved {len(all_context)} context items for query: {query}")
                    return context_text
                else:
                    return "No relevant context found for the query."

            except Exception as e:
                logger.error(f"Failed to retrieve monitoring context: {e}")
                return f"Error retrieving context: {str(e)}"

        @function_tool
        def save_interaction_to_memory(user_message: str, assistant_response: str) -> str:
            """Save a user-assistant interaction to memory.

            Args:
                user_message: The user's message/query
                assistant_response: The assistant's response

            Returns:
                Status message indicating success or failure
            """
            try:
                # Here, we create a memory event that stores the memory for the given
                # memory id, actor id, session id.
                client.create_event(
                    memory_id=memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    messages=[
                        (user_message, "USER"),
                        (assistant_response, "ASSISTANT"),
                    ],
                )
                logger.info("Successfully saved interaction to memory")
                return "Interaction saved to memory successfully."

            except Exception as e:
                logger.error(f"Failed to save interaction to memory: {e}")
                return f"Error saving interaction: {str(e)}"

        @function_tool
        def get_recent_conversation_history(k_turns: int = 5) -> str:
            """Retrieve recent conversation history from memory.

            Args:
                k_turns: Number of recent conversation turns to retrieve (default: 5)

            Returns:
                String containing the recent conversation history
            """
            try:
                # This lists the conversation history for the provided memory id, actor id and session id.
                # this lists the number of recent conversation turns to retrieve.
                recent_turns = client.get_last_k_turns(
                    memory_id=memory_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    k=k_turns,
                )

                if recent_turns:
                    context_messages = []
                    for turn in recent_turns:
                        for message in turn:
                            role = message["role"]
                            content = message["content"]["text"]
                            context_messages.append(f"{role}: {content}")

                    context = "\n".join(context_messages)
                    logger.info(f"Retrieved {len(recent_turns)} conversation turns")
                    return context
                else:
                    return "No recent conversation history found."

            except Exception as e:
                logger.error(f"Failed to retrieve conversation history: {e}")
                return f"Error retrieving history: {str(e)}"

        @function_tool
        def save_custom_memory(content: str, memory_type: str = "SemanticMemory") -> str:
            """Save custom content to a specific memory type.

            Args:
                content: The content to save to memory
                memory_type: The type of memory to save to (default: "SemanticMemory")

            Returns:
                Status message indicating success or failure
            """
            try:
                # Create a single message event for custom content
                client.create_event(
                    memory_id=memory_id,
                    actor_id=actor_id,
                    session_id=f"{session_id}_custom_{int(time.time())}",
                    messages=[(content, "ASSISTANT")],
                )
                logger.info(f"Successfully saved custom content to {memory_type}")
                return f"Custom content saved to {memory_type} successfully."

            except Exception as e:
                logger.error(f"Failed to save custom content: {e}")
                return f"Error saving custom content: {str(e)}"

        @function_tool
        def search_memory_by_namespace(query: str, namespace_type: str, top_k: int = 5) -> str:
            """Search memory within a specific namespace type.

            Args:
                query: The search query
                namespace_type: The namespace type to search in
                top_k: Number of results to return

            Returns:
                String containing search results
            """
            try:
                if namespace_type not in namespaces:
                    available = ", ".join(namespaces.keys())
                    return f"Invalid namespace type. Available types: {available}"

                namespace = namespaces[namespace_type]
                memories = client.retrieve_memories(
                    memory_id=memory_id,
                    namespace=namespace.format(actorId=actor_id),
                    query=query,
                    top_k=top_k,
                )

                results = []
                for memory in memories:
                    if isinstance(memory, dict):
                        content = memory.get("content", {})
                        if isinstance(content, dict):
                            text = content.get("text", "").strip()
                            if text:
                                results.append(text)

                if results:
                    return f"Found {len(results)} results in {namespace_type}:\n" + "\n---\n".join(results)
                else:
                    return f"No results found in {namespace_type} for query: {query}"

            except Exception as e:
                logger.error(f"Failed to search memory: {e}")
                return f"Error searching memory: {str(e)}"

        # Return all the tools
        return [
            # Here, we create the following memory tools
            # The retrieved memory tool is used to retrieve memory
            # based on a strategy across namespaces for the given actor (in this case the actor is the user or the agent)
            retrieve_monitoring_context,
            # This creates a memory event for that given actor and session.
            save_interaction_to_memory,
            # This retrieves the "k" number of conversation turns from the recent conversation for an actor in a specific
            # session
            get_recent_conversation_history,
            save_custom_memory,
            search_memory_by_namespace,
        ]


# Factory functions to create memory tools for specific agents
def create_memory_tools(memory_id: str, client: MemoryClient, actor_id: str, session_id: str):
    """Create memory tools for the lead orchestrator agent"""
    memory_tools = AgentMemoryTools(memory_id, client, actor_id, session_id)
    return memory_tools.create_memory_tools()
