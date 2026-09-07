"""Precedent Agent — retrieves and analyzes past decisions from memory.

Called as a graph node. Queries episodic memory for relevant patterns
(reflections) and policyholder history (episodes), filters out noise,
and explains why each result applies to the current claim.

Information barrier: receives only the claim summary (from graph task),
never sees investigation results. Query constructed from structured
claim fields to prevent contamination.
"""

import json
import logging

from agents.prompts import with_current_date
from memory.config import AGENT_MODEL_ID, HUMAN_GROUNDED_FILTER, REFLECTION_NAMESPACE, episode_namespace_path
from schemas import TypedClaimSummary
from strands import Agent
from strands import tool as strands_tool
from tools import signals

logger = logging.getLogger("claims-demo.precedent")

PRECEDENT_PROMPT = """\
You are the Precedent Agent. Your job is to retrieve past decisions and
policyholder history from memory, then analyze what is relevant to the
current claim.

Your tools:
- search_claim_patterns() — retrieves learned patterns from past human adjuster decisions
- lookup_policyholder_history() — retrieves this policyholder's own prior claim episodes

WORKFLOW:
1. Call both tools.
2. Read the results carefully.
3. Filter out anything irrelevant:
   - Discard patterns about workflow, orchestration, or internal processing
   - Discard patterns about unrelated claim types (e.g. auto patterns for a fire claim)
   - Discard episodes that don't inform this specific claim
4. For each relevant pattern or episode, explain in one sentence WHY it applies
   to this claim.

OUTPUT FORMAT:

RELEVANT PATTERNS:
[1] [pattern title] — [why it applies to this claim]
[2] ...
(or "None found" if nothing relevant)

POLICYHOLDER HISTORY:
[1] [episode summary] — [relevance to current claim]
(or "No prior episodes" if none)

Do NOT make a decision. Do NOT assess fraud or coverage. Just surface relevant
precedents that the adjudicator should consider.
Do NOT use emojis.
"""


def create_precedent_agent(
    claim: TypedClaimSummary,
    memory_id: str,
    memory_client,
    mode: str,
    session_id: str,
) -> Agent:
    refl_filters = HUMAN_GROUNDED_FILTER if mode == "auto" else None
    filter_label = "human_adjuster" if refl_filters else "none"

    @strands_tool
    def search_claim_patterns() -> str:
        """Search for learned patterns from past human adjuster decisions.

        Returns:
            Past decision patterns relevant to the current claim type.
        """
        query_parts = [claim.incident_type, claim.damage_description]
        if claim.reporting_timeline:
            query_parts.append(claim.reporting_timeline)
        if claim.documentation:
            query_parts.append(" ".join(claim.documentation))
        query = " ".join(query_parts)

        try:
            kwargs = {
                "memory_id": memory_id,
                "namespace": REFLECTION_NAMESPACE,
                "query": query,
                "top_k": 5,
            }
            if refl_filters:
                kwargs["metadata_filters"] = refl_filters
            logger.info("search_claim_patterns: query=%r, filter=%s", query, filter_label)
            records = memory_client.retrieve_memories(**kwargs)

            results = []
            trace_records = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                text = rec.get("content", {}).get("text", "").strip()
                if text:
                    title = ""
                    parsed = None
                    try:
                        parsed = json.loads(text)
                        title = parsed.get("title", "")
                    except (ValueError, TypeError):
                        pass
                    results.append(f"[{title or 'Untitled'}]: {text[:400]}")
                    trace_records.append(parsed if parsed else {"text": text[:500]})

            logger.info("search_claim_patterns: %d result(s)", len(results))

            # Record to signals (for Adjuster Console cards)
            signals.record(
                session_id,
                "precedent_patterns",
                {
                    "query": query,
                    "filter": filter_label,
                    "count": len(trace_records),
                    "patterns": [
                        {"title": r.get("title", ""), "confidence": r.get("confidence", "")}
                        for r in trace_records
                        if isinstance(r, dict)
                    ],
                },
            )

            # Write subtool trace with full content (snapshot at decision time)
            if session_id:
                try:
                    memory_client.create_event(
                        memory_id=memory_id,
                        actor_id="system",
                        session_id=session_id,
                        messages=[
                            (
                                json.dumps(
                                    {
                                        "tool": "search_claim_patterns",
                                        "query": query,
                                        "filter": filter_label,
                                        "result_count": len(results),
                                        "results": trace_records,
                                    }
                                ),
                                "TOOL",
                            )
                        ],
                    )
                except Exception as e:  # noqa: BLE001 - best-effort trace; must not break agent flow
                    logger.warning("Subtool trace failed: %s", e)

            if not results:
                return "No patterns found."
            return "\n\n".join(results)
        except Exception as e:  # noqa: BLE001 - tool must return text, never raise into the agent
            logger.error("search_claim_patterns failed: %s", e)
            return "Pattern retrieval failed."

    @strands_tool
    def lookup_policyholder_history() -> str:
        """Look up this policyholder's own prior claim episodes.

        Returns:
            Past claim episodes for this specific policyholder.
        """
        try:
            ns_path = episode_namespace_path(claim.actor_id)
            ep_query = claim.incident_type
            logger.info("lookup_policyholder_history: actor=%s, query=%r", claim.actor_id, ep_query)
            kwargs = {
                "memory_id": memory_id,
                "namespace_path": ns_path,
                "query": ep_query,
                "top_k": 3,
            }
            records = memory_client.retrieve_memories(**kwargs)

            results = []
            trace_records = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                text = rec.get("content", {}).get("text", "").strip()
                if text:
                    results.append(text[:400])
                    parsed = None
                    try:
                        parsed = json.loads(text)
                    except (ValueError, TypeError):
                        pass
                    trace_records.append(parsed if parsed else {"text": text[:500]})

            logger.info("lookup_policyholder_history: %d episode(s)", len(results))

            # Record to signals (for Adjuster Console cards)
            signals.record(
                session_id,
                "policyholder_episodes",
                {
                    "actor_id": claim.actor_id,
                    "query": ep_query,
                    "count": len(trace_records),
                    "episodes": [
                        {"situation": r.get("situation", "")[:200]} for r in trace_records if isinstance(r, dict)
                    ],
                },
            )

            # Write subtool trace with full content
            if session_id:
                try:
                    memory_client.create_event(
                        memory_id=memory_id,
                        actor_id="system",
                        session_id=session_id,
                        messages=[
                            (
                                json.dumps(
                                    {
                                        "tool": "lookup_policyholder_history",
                                        "query": ep_query,
                                        "filter": filter_label,
                                        "result_count": len(results),
                                        "results": trace_records,
                                    }
                                ),
                                "TOOL",
                            )
                        ],
                    )
                except Exception as e:  # noqa: BLE001 - best-effort trace; must not break agent flow
                    logger.warning("Subtool trace failed: %s", e)

            if not results:
                return "No prior episodes for this policyholder."
            return "\n\n".join(results)
        except Exception as e:  # noqa: BLE001 - tool must return text, never raise into the agent
            logger.error("lookup_policyholder_history failed: %s", e)
            return "Episode retrieval failed."

    return Agent(
        name="precedent",
        model=AGENT_MODEL_ID,
        system_prompt=with_current_date(PRECEDENT_PROMPT),
        tools=[search_claim_patterns, lookup_policyholder_history],
    )
