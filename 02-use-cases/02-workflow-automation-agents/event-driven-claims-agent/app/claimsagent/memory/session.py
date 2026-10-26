"""AgentCore Memory session manager with graceful degradation.

The session manager attaches to the Strands Agent and automatically records
each conversation turn to AgentCore Memory. The SEMANTIC strategy enables
cross-session recall (e.g., prior claims for repeat claimants), while
SUMMARIZATION compresses session history to prevent context overflow.

If Memory is not deployed or unavailable (local dev, pre-deploy), the agent
continues working without memory — it just won't recall prior interactions.
"""

from typing import Optional

from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from config import MEMORY_ID, MEMORY_RETRIEVAL_RELEVANCE, MEMORY_RETRIEVAL_TOP_K, REGION


def get_memory_session_manager(session_id: str, actor_id: str) -> Optional[AgentCoreMemorySessionManager]:
    """Create a session manager bound to a specific session and actor.

    Args:
        session_id: Unique session identifier (e.g., claim-{policy_number}-{timestamp}).
        actor_id: The claimant or user who initiated the interaction.

    Returns:
        AgentCoreMemorySessionManager if MEMORY_ID is configured, else None.
    """
    if not MEMORY_ID:
        return None

    # Retrieval config aligned with agentcore.json memory namespaces:
    #   - claims/{actorId}/facts (SEMANTIC) — prior claim history for this claimant
    #   - claims/{actorId}/{sessionId} (SUMMARIZATION) — session summaries
    retrieval_config = {
        f"claims/{actor_id}/facts": RetrievalConfig(
            top_k=MEMORY_RETRIEVAL_TOP_K, relevance_score=MEMORY_RETRIEVAL_RELEVANCE
        ),
        f"claims/{actor_id}/{session_id}": RetrievalConfig(
            top_k=max(MEMORY_RETRIEVAL_TOP_K - 2, 1), relevance_score=MEMORY_RETRIEVAL_RELEVANCE
        ),
    }

    return AgentCoreMemorySessionManager(
        AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config=retrieval_config,
        ),
        REGION,
    )
