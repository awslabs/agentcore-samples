"""Memory enrichment: retrieve past-incident context for the system prompt.

Uses AgentCore Memory's data-plane retrieval to pull semantically-relevant
summaries of past incidents for a given requester. The configured
`summary_memory_strategy` (SUMMARIZATION at "incidents/{actorId}/{sessionId}")
rolls each session into a summary; this function searches those summaries.

This transforms Memory from a passive session logger into an active
enrichment source — the agent sees prior context before it reasons,
enabling it to detect recurring incidents and escalate appropriately.
"""

import logging
import re
import threading
from typing import Optional

from bedrock_agentcore.memory import MemoryClient
from config import MEMORY_ID, REGION

logger = logging.getLogger(__name__)

_memory_client: MemoryClient | None = None
_memory_client_lock = threading.Lock()


def _get_memory_client() -> MemoryClient | None:
    """Lazy-init the MemoryClient (thread-safe, avoids import-time failures in local dev)."""
    global _memory_client
    if _memory_client is None and MEMORY_ID:
        with _memory_client_lock:
            if _memory_client is None:
                _memory_client = MemoryClient(region_name=REGION)
    return _memory_client


def retrieve_past_incident_records(requester_id: str, query: str, top_k: int = 5) -> list[dict]:
    """Pull summarized past-incident episodes for this requester.

    The Memory resource is configured with a `summary_memory_strategy`
    namespaced as `incidents/{actorId}/{sessionId}` ({sessionId} is mandatory
    for SUMMARIZATION strategies). AgentCore extracts a summary per session
    asynchronously after each event; this function does a semantic search across
    all of a requester's session summaries by querying the `incidents/{actorId}`
    path prefix (via `namespace_path`).

    Returns records with content and Memory record IDs (most relevant first).
    Retrieval failures propagate: missing history must not masquerade as none.
    """
    if not isinstance(requester_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", requester_id):
        raise ValueError("Requester must be an opaque identifier, not PII or a namespace path")
    client = _get_memory_client()
    if not client:
        raise RuntimeError("Memory is required for incident enrichment")

    # The summary strategy stores records at "incidents/{actorId}/{sessionId}"
    # ({sessionId} is mandatory for SUMMARIZATION strategies). Retrieve with a
    # hierarchical PATH PREFIX (namespace_path) so all of a requester's session
    # summaries match — an exact `namespace` match would never hit the
    # session-scoped leaf namespaces and would silently return nothing.
    namespace_path = f"incidents/{requester_id}"
    try:
        # The high-level retrieve_memories helper swallows ClientError and returns
        # []. Use the SDK's forwarded data-plane operation so denial is not 'no history'.
        hits = client.retrieve_memory_records(
            memoryId=MEMORY_ID,
            namespace_path=namespace_path,
            searchCriteria={"searchQuery": query, "topK": top_k},
        )["memoryRecordSummaries"]
    except Exception:  # noqa: BLE001 -- require review on any unknown Memory failure; redact response
        raise RuntimeError("Memory retrieval failed; incident requires retry/review") from None
    return [hit for hit in hits or [] if (hit.get("content") or {}).get("text")]


def retrieve_past_incidents(requester_id: str, query: str, top_k: int = 5) -> list[str]:
    """Compatibility helper, keeping the same cross-session request."""
    return [hit["content"]["text"] for hit in retrieve_past_incident_records(requester_id, query, top_k)]


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
