"""Semantic memory strategy — extracting standalone facts.

What you learn:
    - Configure `semanticMemoryStrategy` on CreateMemory
    - Drive a short conversation, wait for asynchronous extraction
    - Retrieve facts back via RetrieveMemoryRecords (vector search)

Semantic strategy extracts standalone facts about the user or the world
("user's name is Alex", "based in Berlin"). It is the default choice for
"who is this user?" recall.

Two ways to run it:

    python semantic.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python semantic.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

Use `boto3` to see the underlying calls. Use `sdk` for real work, since it handles the
boilerplate for you. The `sdk` path needs bedrock-agentcore 1.14 or newer, because it
searches with `search_long_term_memories(namespace=...)`. Older versions only accept the
deprecated `namespace_prefix=`.

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

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
SESSION_ID = f"sess-{int(time.time())}"
EXTRACTION_WAIT_SECONDS = 90
NAMESPACE_TEMPLATE = "/users/{actorId}/facts/"

TURNS = [
    ("USER", "Hi, I'm Alex. I'm based in Berlin and I work as a backend engineer."),
    ("ASSISTANT", "Nice to meet you, Alex."),
    ("USER", "I prefer Python over Java for most things, but I write Rust for performance-critical code."),
    ("ASSISTANT", "Good to know."),
    ("USER", "Also, I'm allergic to peanuts."),
    ("ASSISTANT", "I'll keep that in mind."),
]
QUERIES = [
    "What programming languages does the user prefer?",
    "Where is the user based?",
    "Any dietary restrictions?",
]


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"Semantic_{int(time.time())}",
        description="Semantic strategy tutorial (boto3)",
        eventExpiryDuration=30,
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "UserFacts",
                    "description": "Standalone facts about the user",
                    "namespaces": [NAMESPACE_TEMPLATE],
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

    for role, text in TURNS:
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": role, "content": {"text": text}}}],
        )
    print(f"[boto3] Waiting {EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(EXTRACTION_WAIT_SECONDS)

    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    for query in QUERIES:
        hits = data.retrieve_memory_records(
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": 3},
        )["memoryRecordSummaries"]
        print(f"\n[boto3] Q: {query}")
        for h in hits:
            print(f"  - {h['content']['text']} (score={h.get('score')})")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\n[boto3] Deleted memory {memory_id}")
    else:
        print(f"\n[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
def run_with_sdk(cleanup: bool = False) -> None:
    # MemoryClient owns the control plane (create/delete the resource);
    # MemorySessionManager is data-plane only, so we create the memory with
    # MemoryClient, then drive events + retrieval through a MemorySession.
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"SemanticSession_{int(time.time())}",
        description="Semantic strategy (SDK session API)",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "UserFacts",
                    "description": "Standalone facts about the user",
                    # Current field is namespaceTemplates (namespaces is deprecated).
                    "namespaceTemplates": [NAMESPACE_TEMPLATE],
                }
            }
        ],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    # Bind a session, then write all turns in one add_turns call. add_turns
    # takes ConversationalMessage objects and maps to a single create_event.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)
    session.add_turns(messages=[ConversationalMessage(text, MessageRole[role]) for role, text in TURNS])
    print(f"[sdk] Waiting {EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(EXTRACTION_WAIT_SECONDS)

    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    for query in QUERIES:
        # Use namespace= (exact match); namespace_prefix= is deprecated.
        hits = session.search_long_term_memories(query=query, namespace=namespace, top_k=3)
        print(f"\n[sdk] Q: {query}")
        for h in hits:
            # Each hit is a MemoryRecord (dict-like): content.text + score.
            print(f"  - {h['content']['text']} (score={h.get('score')})")

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
