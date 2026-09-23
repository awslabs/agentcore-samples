"""Session management: creates AgentCoreMemorySessionManager for the Strands agent.

The session manager attaches to the Strands Agent and automatically records
each conversation turn to AgentCore Memory. The SUMMARIZATION strategy
(configured on the Memory resource) rolls sessions into per-requester
summaries that power the enrichment module.
"""

from typing import Optional

from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from config import MEMORY_ID, REGION


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """Create a session manager bound to a specific session and actor.

    Args:
        session_id: Unique session identifier (ticket_id or conversation ID).
        actor_id: The requester/user who initiated the interaction.

    Returns:
        AgentCoreMemorySessionManager if MEMORY_ID is configured, else None.
    """
    if not MEMORY_ID:
        return None

    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
        ),
        REGION,
    )
