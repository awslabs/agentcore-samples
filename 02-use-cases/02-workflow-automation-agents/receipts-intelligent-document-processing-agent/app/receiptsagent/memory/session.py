"""AgentCore Memory session manager with graceful degradation (spec §5.2).

The session manager attaches to the Strands Agent and records each turn to
AgentCore Memory. The SEMANTIC strategy enables cross-receipt recall (frequent
merchants, a user's corrected categories); SUMMARIZATION compresses session
history. Both use custom `receipts/{actorId}/...` namespaces.

If Memory is not deployed (local dev, pre-deploy), the agent continues without
it — it just won't recall prior context. Never let a missing memory hang a run.
"""

from typing import Optional

from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from config import MEMORY_ID, REGION


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """Create a session manager bound to a session + actor (the user_id).

    Returns None when MEMORY_ID is unset so callers degrade gracefully.
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
