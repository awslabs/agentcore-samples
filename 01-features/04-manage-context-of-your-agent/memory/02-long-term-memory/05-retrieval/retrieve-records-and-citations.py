"""Retrieving long-term memory records.

What you learn:
    - RetrieveMemoryRecords for semantic search inside a namespace
    - ListMemoryRecords to enumerate without a query
    - GetMemoryRecord to read a single record by id
    - Reading the score, namespaces, and metadata returned with each hit

Two ways to run it:
    python retrieve-records-and-citations.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python retrieve-records-and-citations.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

The `sdk` path needs bedrock-agentcore 1.14 or newer, because it searches with
`search_long_term_memories(namespace=...)`. Older versions only accept the deprecated
`namespace_prefix=`.

Note on "citations": there is no dedicated citation object in the API. A
citation is assembled from the fields returned on each memory record — the
score, the namespaces it lives under, and its memoryRecordId — alongside the
record text. Both runs below build that same citation-style output.

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
EXTRACTION_WAIT_SECONDS = 100  # polling budget; semantic extraction measured ~93s
SESSION_EXTRACTION_WAIT_SECONDS = 90  # semantic extraction surfaces ~60-90s; extra margin
NAMESPACE_TEMPLATE = "/users/{actorId}/facts/"

TURNS = [
    ("USER", "I'm Alex; I'm based in Berlin and I prefer Python."),
    ("ASSISTANT", "Got it."),
    ("USER", "I'm allergic to peanuts and avoid dairy when I can."),
    ("ASSISTANT", "Noted."),
    ("USER", "I take the U-Bahn daily; I don't own a car."),
    ("ASSISTANT", "Understood."),
]


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"Retrieval_{int(time.time())}",
        description="Retrieval tutorial (boto3)",
        eventExpiryDuration=30,
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "Facts",
                    "namespaceTemplates": [NAMESPACE_TEMPLATE],
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
    # Extraction is asynchronous and its latency varies — poll instead of sleeping a
    # fixed amount, so a slow run still shows records instead of printing an empty list.
    print(f"[boto3] Polling up to {EXTRACTION_WAIT_SECONDS}s for extraction...")
    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    deadline = time.time() + EXTRACTION_WAIT_SECONDS
    while True:
        semantic = data.retrieve_memory_records(
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": "dietary restrictions", "topK": 5},
        )["memoryRecordSummaries"]
        if semantic or time.time() >= deadline:
            break
        time.sleep(10)
    print(f"\n[boto3] Semantic search 'dietary restrictions' ({len(semantic)}):")
    for h in semantic:
        print(f"  - score={h.get('score'):.3f} | {h['content']['text']}")

    listed = data.list_memory_records(memoryId=memory_id, namespace=namespace)["memoryRecordSummaries"]
    print(f"\n[boto3] ListMemoryRecords ({len(listed)}):")
    for h in listed:
        print(f"  - {h['memoryRecordId']}: {h['content']['text']}")

    if listed:
        full = data.get_memory_record(memoryId=memory_id, memoryRecordId=listed[0]["memoryRecordId"])["memoryRecord"]
        print("\n[boto3] GetMemoryRecord (one):")
        print(f"  id={full['memoryRecordId']}")
        print(f"  text={full['content']['text']}")
        print(f"  createdAt={full.get('createdAt')}")

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
        name=f"RetrievalSession_{int(time.time())}",
        description="Retrieval tutorial (SDK session API)",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "Facts",
                    # Current field is namespaceTemplates (namespaces is deprecated).
                    "namespaceTemplates": [NAMESPACE_TEMPLATE],
                }
            }
        ],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)
    session.add_turns(messages=[ConversationalMessage(text, MessageRole[role]) for role, text in TURNS])
    print(f"[sdk] Waiting {SESSION_EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(SESSION_EXTRACTION_WAIT_SECONDS)

    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    # Use namespace= (exact match); namespace_prefix= is deprecated.
    semantic = session.search_long_term_memories(
        query="dietary restrictions",
        namespace=namespace,
        top_k=5,
    )
    print(f"\n[sdk] Semantic search 'dietary restrictions' ({len(semantic)}):")
    # There is no citation object: a citation is built from the fields on each
    # MemoryRecord — score, namespaces, and memoryRecordId — plus the text.
    for h in semantic:
        print(f"  - score={h.get('score'):.3f} | {h['content']['text']}")
        print(f"      cite: id={h['memoryRecordId']} ns={','.join(h.get('namespaces', []))}")

    # list_long_term_memory_records enumerates a namespace without a query;
    # get_memory_record reads one record by id (the citation's anchor).
    listed = session.list_long_term_memory_records(namespace=namespace)
    print(f"\n[sdk] ListLongTermMemoryRecords ({len(listed)}):")
    for h in listed:
        print(f"  - {h['memoryRecordId']}: {h['content']['text']}")

    if listed:
        full = session.get_memory_record(listed[0]["memoryRecordId"])
        print("\n[sdk] GetMemoryRecord (one):")
        print(f"  id={full['memoryRecordId']}")
        print(f"  text={full['content']['text']}")
        print(f"  createdAt={full.get('createdAt')}")

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
