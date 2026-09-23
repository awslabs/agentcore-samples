"""
Knowledge Base RAG Agent - Main Entry Point

This module provides the entry point for the AgentCore Runtime.
It wraps the Strands agent with BedrockAgentCoreApp for deployment.

Based on: https://strandsagents.com/latest/documentation/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/
"""

import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from src.agent import create_agent
from src.memory import MemoryManager

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Create the AgentCore app
app = BedrockAgentCoreApp()

# Initialize the agent (lazy initialization)
_agent = None
_memory_manager = None


def get_agent():
    """Lazy initialization of the agent."""
    global _agent
    if _agent is None:
        _agent = create_agent()
    return _agent


def get_memory_manager():
    """Lazy initialization of the memory manager."""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager(
            memory_id=os.environ.get("MEMORY_ID"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
        )
    return _memory_manager


@app.entrypoint
def invoke(payload: dict) -> dict:
    """
    Handler for agent invocation.

    Args:
        payload: Request payload containing:
            - prompt: User message
            - session_id: Optional session ID for conversation continuity
            - user_id: Optional user ID for personalization

    Returns:
        Response containing the agent's reply
    """
    try:
        # Extract request parameters
        prompt = payload.get("prompt", "")
        session_id = payload.get("session_id", "default")
        user_id = payload.get("user_id", "anonymous")

        if not prompt:
            return {
                "response": "Please provide a prompt.",
                "session_id": session_id,
                "status": "error",
            }

        logger.info(f"Processing request - session: {session_id}, user: {user_id}")

        # Get agent and memory manager
        agent = get_agent()
        memory_manager = get_memory_manager()

        # Retrieve conversation history from memory
        conversation_history = memory_manager.get_conversation_history(
            actor_id=user_id, session_id=session_id, max_results=10
        )

        # Retrieve relevant long-term memories
        relevant_memories = memory_manager.retrieve_memories(actor_id=user_id, query=prompt)

        # Build context for the agent
        context = _build_context(conversation_history, relevant_memories)

        # Invoke the agent
        if context:
            full_prompt = f"{context}\n\nUser: {prompt}"
        else:
            full_prompt = prompt

        response = agent(full_prompt)

        # Extract response text from Strands agent response
        if hasattr(response, "message") and isinstance(response.message, dict):
            content = response.message.get("content", [])
            if content and isinstance(content, list) and len(content) > 0:
                response_text = content[0].get("text", str(response))
            else:
                response_text = str(response)
        else:
            response_text = str(response)

        # Store the interaction in memory
        memory_manager.store_interaction(
            actor_id=user_id,
            session_id=session_id,
            user_message=prompt,
            assistant_message=response_text,
        )

        logger.info(f"Request completed - session: {session_id}")

        return {
            "response": response_text,
            "session_id": session_id,
            "status": "success",
        }

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return {
            "response": f"An error occurred: {str(e)}",
            "session_id": payload.get("session_id", "default"),
            "status": "error",
        }


def _build_context(conversation_history: list, relevant_memories: list) -> str:
    """Build context string from conversation history and memories."""
    context_parts = []

    # Add relevant long-term memories
    if relevant_memories:
        context_parts.append("Relevant information from previous conversations:")
        for memory in relevant_memories[:5]:  # Limit to 5 most relevant
            context_parts.append(f"- {memory}")
        context_parts.append("")

    # Add recent conversation history
    if conversation_history:
        context_parts.append("Recent conversation:")
        for turn in conversation_history[-5:]:  # Last 5 turns
            role = turn.get("role", "unknown")
            content = turn.get("content", "")
            context_parts.append(f"{role.capitalize()}: {content}")
        context_parts.append("")

    return "\n".join(context_parts) if context_parts else ""


if __name__ == "__main__":
    # Run the AgentCore app
    app.run()
