"""Memory enrichment: retrieve past-incident context for the system prompt.

Uses AgentCore Memory's `retrieve_memories` to pull semantically-relevant
summaries of past incidents for a given requester. The configured
`summary_memory_strategy` (SUMMARIZATION type, namespace "incidents/{actorId}")
rolls each session into a summary; this function searches those summaries.

This transforms Memory from a passive session logger into an active
enrichment source — the agent sees prior context before it reasons,
enabling it to detect recurring incidents and escalate appropriately.
"""

import logging
import threading
from typing import Optional

from bedrock_agentcore.memory import MemoryClient
from config import MEMORY_ID, REGION

logger = logging.getLogger(__name__)

_memory_client: Optional[MemoryClient] = None
_memory_client_lock = threading.Lock()


def _get_memory_client() -> Optional[MemoryClient]:
    """Lazy-init the MemoryClient (thread-safe, avoids import-time failures in local dev)."""
    global _memory_client
    if _memory_client is None and MEMORY_ID:
        with _memory_client_lock:
            if _memory_client is None:
                _memory_client = MemoryClient(region_name=REGION)
    return _memory_client


def retrieve_past_incidents(requester_id: str, query: str, top_k: int = 5) -> list[str]:
    """Pull summarized past-incident episodes for this requester.

    The Memory resource is configured with a `summary_memory_strategy`
    namespaced as `incidents/{actorId}/{sessionId}` ({sessionId} is mandatory
    for SUMMARIZATION strategies). AgentCore extracts a summary per session
    asynchronously after each event; this function does a semantic search across
    all of a requester's session summaries by querying the `incidents/{actorId}`
    path prefix (via `namespace_path`).

    Returns a list of summary strings (most relevant first), or empty
    list on failure (graceful degradation — the agent just won't have
    prior context).
    """
    client = _get_memory_client()
    if not client:
        logger.info("Memory not configured (MEMORY_ID unset) — no past-incident context")
        return []

    # The summary strategy stores records at "incidents/{actorId}/{sessionId}"
    # ({sessionId} is mandatory for SUMMARIZATION strategies). Retrieve with a
    # hierarchical PATH PREFIX (namespace_path) so all of a requester's session
    # summaries match — an exact `namespace` match would never hit the
    # session-scoped leaf namespaces and would silently return nothing.
    namespace_path = f"incidents/{requester_id}"
    try:
        hits = client.retrieve_memories(
            memory_id=MEMORY_ID,
            namespace_path=namespace_path,
            query=query,
            top_k=top_k,
        )
    except Exception as exc:
        logger.warning("Memory retrieve_memories failed for actor=%s: %s", requester_id, exc)
        return []

    summaries = []
    for hit in hits or []:
        text = (hit.get("content") or {}).get("text") or ""
        if text:
            summaries.append(text)

    logger.info("Retrieved %d past-incident summaries for %s", len(summaries), requester_id)
    return summaries


def format_past_incidents_block(past_incidents: list[str]) -> str:
    """Format past-incident summaries for injection into the system prompt."""
    if past_incidents:
        body = "\n".join(f"  - {s}" for s in past_incidents)
        return (
            f"Past incidents for this requester (most-relevant first, "
            f"count={len(past_incidents)}):\n{body}\n\n"
            "If the requester has 2+ prior incidents on the same service/topic, "
            "this is a recurring issue — mention it in your resolution and escalate."
        )
    return "Past incidents for this requester: none on file."
