"""Episodic memory strategy — meaningful interaction sequences.

What you learn:
    - Configure `episodicMemoryStrategy` on CreateMemory
    - Drive a multi-turn interaction (incl. a TOOL turn) that has a beginning/middle/end
    - Retrieve episodes + reflections via RetrieveMemoryRecords (hierarchical namespace)

Episodic strategy captures "episodes" — meaningful sequences of turns
that hang together as one event in the user's life ("debugged a memory
leak in service X on Tuesday"). It also adds a *reflection* step that
generates cross-episode insights.

Episode-completion signal: extraction only fires once the service detects the
episode has CONCLUDED. Completion uses next-turn lookahead, so each episode below
ends with an explicit closing turn AND a trailing turn confirming it — a final
turn with nothing after it reads as "still in progress" and never extracts.

Two ways to run it:
    python episodic.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python episodic.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

The `sdk` path needs bedrock-agentcore 1.14 or newer, because it retrieves with
`search_long_term_memories(namespace_path=...)`. Older versions only accept the
deprecated `namespace_prefix=`.

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

SDK note: `MemoryClient.add_episodic_strategy()` exists, but here we pass the
raw `episodicMemoryStrategy` shape to `create_memory_and_wait` so the boto3 and
sdk paths stay 1:1. The episodic strategy REQUIRES a `reflectionConfiguration`
whose namespace is the same as (or a prefix of) the episode namespace —
omitting it makes CreateMemory fail.

Namespaces: episodes are written per session (more nested) and reflections roll
up per actor (the prefix). We retrieve across that hierarchy with `namespacePath`
(`namespace_path=` in the SDK), which returns records under the same parent —
both the session-scoped episodes and the actor-scoped reflections.

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone

REGION = os.getenv("AWS_REGION", "us-east-1")
ACTOR_ID = "user-alex"
# Episodic runs a 3-step pipeline (Extraction -> Consolidation -> Reflection) and
# only emits records once it detects a COMPLETED episode, so it is much slower than
# single-step semantic extraction — allow ~15-20 min, not seconds. This is an UPPER
# BOUND: both paths poll and return as soon as the first record appears, so a fast
# extraction won't wait the full duration.
EXTRACTION_WAIT_SECONDS = 1200
# Episodes are scoped per actor+session (more nested); reflections roll up to the
# actor level. The reflection namespace MUST be the same as (or a prefix of) the
# episode namespace — here it's the parent of it.
EPISODE_NAMESPACE_TEMPLATE = "/episodes/{actorId}/{sessionId}/"
REFLECTION_NAMESPACE_TEMPLATE = "/episodes/{actorId}/"
# Retrieval/waiting happens at the actor level so it spans both the session-scoped
# episodes and the actor-scoped reflections (RetrieveMemoryRecords matches by prefix).
ACTOR_NAMESPACE_TEMPLATE = "/episodes/{actorId}/"

DEBUG_TURNS = [
    ("USER", "I'm seeing a memory leak in the payment service after the last deploy."),
    ("ASSISTANT", "When did the leak start?"),
    ("USER", "Right after we shipped the new caching layer on Monday."),
    ("ASSISTANT", "Have you checked for unbounded growth in the cache?"),
    # A TOOL turn carries tool results back into the episode. The TOOL role is the
    # highest-value context for episodic extraction — it records what the agent
    # actually observed, not just what was said. (Role enum: USER/ASSISTANT/TOOL/OTHER.)
    ("TOOL", "heap_profiler(service=payment): cache entries=4.2M, evictions=0, ttl=none"),
    ("USER", "Yes — found it. The TTL was unset; it's now fixed in v2.4.1."),
    ("ASSISTANT", "Great catch. I'll remember that the cache TTL was the culprit."),
]
DESIGN_TURNS = [
    ("USER", "Designing the new notifications service. Start with email or push?"),
    ("ASSISTANT", "What's the primary user persona?"),
    ("USER", "Mobile-first consumers."),
    ("ASSISTANT", "Then push-first makes sense; layer email later for transactional confirmations."),
    ("USER", "Agreed — we'll go push-first with FCM and APNs. That settles the design."),
    # Trailing ASSISTANT turn AFTER the closing USER turn. Episode-completion detection
    # uses next-turn lookahead: a final USER turn with no following turn reads as "still
    # in progress" and no episode forms. This confirming turn is what marks the episode
    # COMPLETE (same gate as DEBUG_TURNS' closer).
    ("ASSISTANT", "Sounds good — push-first with FCM and APNs it is. I'll note that decision."),
]
QUERIES = ["memory leak debugging", "notifications design decisions"]


def _wait_for_records_boto3(data, memory_id, namespace_path, query, *, max_wait, poll=30) -> bool:
    """Bounded poll for the first extracted record (boto3 path).

    boto3 has no built-in extraction waiter, and this path deliberately
    demonstrates the raw data plane without the SDK. So we mirror what the SDK's
    MemoryClient.wait_for_memories does — poll RetrieveMemoryRecords until a record
    appears or the deadline passes — returning early instead of a blind sleep.
    Reliable here because the namespace starts EMPTY (fresh memory). We use
    `namespacePath` so the actor-level path spans the session-scoped episodes.
    """
    deadline = time.time() + max_wait
    while time.time() < deadline:
        hits = data.retrieve_memory_records(
            memoryId=memory_id,
            namespacePath=namespace_path,
            searchCriteria={"searchQuery": query, "topK": 1},
        )["memoryRecordSummaries"]
        if hits:
            return True
        time.sleep(poll)
    return False


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"Episodic_{int(time.time())}",
        description="Episodic strategy (boto3)",
        eventExpiryDuration=30,
        memoryStrategies=[
            {
                "episodicMemoryStrategy": {
                    "name": "Episodes",
                    "description": "Meaningful interaction sequences",
                    "namespaceTemplates": [EPISODE_NAMESPACE_TEMPLATE],
                    # REQUIRED for episodic — the Reflection step writes cross-episode
                    # insights here; namespace must be the same-as/prefix-of the episode ns.
                    "reflectionConfiguration": {"namespaceTemplates": [REFLECTION_NAMESPACE_TEMPLATE]},
                }
            }
        ],
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    for session_id, turns in [
        (f"debug-{int(time.time())}", DEBUG_TURNS),
        (f"design-{int(time.time())}", DESIGN_TURNS),
    ]:
        for role, text in turns:
            data.create_event(
                memoryId=memory_id,
                actorId=ACTOR_ID,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"role": role, "content": {"text": text}}}],
            )

    namespace_path = ACTOR_NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    print(f"[boto3] Waiting up to {EXTRACTION_WAIT_SECONDS}s for extraction + reflection...")
    if _wait_for_records_boto3(data, memory_id, namespace_path, QUERIES[0], max_wait=EXTRACTION_WAIT_SECONDS):
        print("[boto3] Episode records available.")
    else:
        print(f"[boto3] No records after {EXTRACTION_WAIT_SECONDS}s (episodic can lag; try again later).")

    for query in QUERIES:
        hits = data.retrieve_memory_records(
            memoryId=memory_id,
            namespacePath=namespace_path,
            searchCriteria={"searchQuery": query, "topK": 3},
        )["memoryRecordSummaries"]
        print(f"\n[boto3] Q: {query}")
        for h in hits:
            print(f"  - {h['content']['text']}")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\n[boto3] Deleted memory {memory_id}")
    else:
        print(f"\n[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
def run_with_sdk(cleanup: bool = False) -> None:
    # MemoryClient owns the control plane (create/delete the resource);
    # MemorySessionManager is data-plane only, so we create the memory with
    # MemoryClient, then drive events + retrieval through the manager / its sessions.
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    # Episodic REQUIRES reflectionConfiguration. The reflection namespace must be
    # the same as (or a prefix of) the episode namespaceTemplates; otherwise
    # CreateMemory throws a ValidationException. namespaces is deprecated — both
    # the strategy and its reflection block use namespaceTemplates.
    memory = client.create_memory_and_wait(
        name=f"EpisodicSession_{int(time.time())}",
        description="Episodic strategy (SDK session API)",
        strategies=[
            {
                "episodicMemoryStrategy": {
                    "name": "Episodes",
                    "description": "Meaningful interaction sequences",
                    "namespaceTemplates": [EPISODE_NAMESPACE_TEMPLATE],
                    "reflectionConfiguration": {
                        "namespaceTemplates": [REFLECTION_NAMESPACE_TEMPLATE],
                    },
                }
            }
        ],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    # Each scenario is its own session. add_turns takes ConversationalMessage
    # objects and maps to a single create_event. Episodes only consolidate once
    # the interaction has a clear conclusion, so each scenario closes with an
    # explicit ending plus a confirming turn (see DEBUG_TURNS / DESIGN_TURNS).
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    for session_id, turns in [
        (f"debug-session-{int(time.time())}", DEBUG_TURNS),
        (f"design-session-{int(time.time())}", DESIGN_TURNS),
    ]:
        session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=session_id)
        session.add_turns(messages=[ConversationalMessage(text, MessageRole[role]) for role, text in turns])

    # Retrieve at the actor level so the search spans BOTH the session-scoped
    # episodes (/episodes/{actorId}/{sessionId}/) and the actor-scoped reflections
    # (/episodes/{actorId}/). search_long_term_memories lives on the manager itself
    # (it is memory-scoped, not bound to one session). namespace_path = hierarchical
    # prefix match; namespace= would be exact-match only.
    namespace_path = ACTOR_NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)

    # Episodic extraction + reflection is slow (often 15-20 min) and its exact
    # timing varies, so poll instead of sleeping a fixed amount: ask for the first
    # records and return as soon as they appear, up to a cap. A blind sleep either
    # wastes time or, if it ends too early, prints nothing even though the records
    # surface moments later.
    print(f"[sdk] Waiting up to {EXTRACTION_WAIT_SECONDS}s for extraction + reflection...")
    deadline = time.time() + EXTRACTION_WAIT_SECONDS
    while time.time() < deadline:
        if manager.search_long_term_memories(query=QUERIES[0], namespace_path=namespace_path, top_k=1):
            print("[sdk] Episode records available.")
            break
        time.sleep(30)
    else:
        print(f"[sdk] No records after {EXTRACTION_WAIT_SECONDS}s (episodic can lag; try again later).")

    for query in QUERIES:
        hits = manager.search_long_term_memories(query=query, namespace_path=namespace_path, top_k=3)
        print(f"\n[sdk] Q: {query}")
        for h in hits:
            # Each hit is a MemoryRecord (dict-like): content.text + score.
            print(f"  - {h['content']['text']}")

    if cleanup:
        client.delete_memory_and_wait(memory_id=memory_id)
        print(f"\n[sdk] Deleted memory {memory_id}")
    else:
        print(f"\n[sdk] Keeping memory {memory_id} (pass --cleanup to delete)")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--cleanup"]
    cleanup = "--cleanup" in sys.argv[1:]
    mode = args[0] if args else "boto3"
    if mode == "boto3":
        run_with_boto3(cleanup=cleanup)
    elif mode == "sdk":
        run_with_sdk(cleanup=cleanup)
    else:
        print(f"Unknown mode {mode!r}. Use boto3 | sdk.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
