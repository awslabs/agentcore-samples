"""Skipping long-term memory extraction for specific events.

What you learn:
    - Use extractionMode="SKIP" on CreateEvent to store an event in short-term
      memory without triggering long-term extraction
    - Verify that skipped events do not produce memory records
    - Common use cases: bulk import, system messages, sensitive turns

The flow:
    1. Create a memory with a semantic strategy
    2. Send 8 events normally (extraction will run)
    3. Send 4 events with extractionMode="SKIP" (no extraction)
    4. Wait for extraction to complete
    5. Retrieve records — only the non-skipped events produce results

Two ways to run it:
    python skip-extraction.py boto3   # direct service calls
    python skip-extraction.py sdk     # AgentCore MemoryClient helpers

The sdk run sends events with `MemoryClient.create_event(extraction_mode=...)` rather
than the session API. `MemorySession.add_turns()` is the usual SDK way to write turns,
but it does not accept `extraction_mode`, and choosing that per event is the whole
lesson here.

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
ACTOR_ID = "user-42"
SESSION_ID = f"sess-skip-{int(time.time())}"
EXTRACTION_WAIT_SECONDS = 90
NAMESPACE_TEMPLATE = "/users/{actorId}/facts/"

STRATEGY = {
    "semanticMemoryStrategy": {
        "name": "UserFacts",
        "namespaces": [NAMESPACE_TEMPLATE],
    }
}

# Normal events: these WILL be extracted into long-term memory.
NORMAL_TURNS = [
    ("USER", "My name is Alex. I'm a software engineer."),
    ("ASSISTANT", "Nice to meet you, Alex! What languages do you work with?"),
    ("USER", "I mostly write Python. It's my favorite language."),
    ("ASSISTANT", "Python is great! Where are you based?"),
    ("USER", "I live in Berlin, Germany. I moved here two years ago."),
    ("ASSISTANT", "Berlin is a fantastic city for tech. Anything else I should know?"),
    ("USER", "Yes — I'm allergic to peanuts. Please keep that in mind."),
    ("ASSISTANT", "Noted, I'll remember your peanut allergy."),
]

# Skipped events: stored in short-term memory but NOT extracted into long-term.
SKIPPED_TURNS = [
    ("USER", "I just won the lottery and my bank account number is 123456789."),
    ("ASSISTANT", "That's exciting! Congratulations on winning."),
    ("USER", "My social security number is 987-65-4321, can you store that for me?"),
    ("ASSISTANT", "I've noted that information for you."),
]


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"SkipExtraction_{int(time.time())}",
        description="Demonstrates extractionMode=SKIP (boto3)",
        eventExpiryDuration=30,
        memoryStrategies=[STRATEGY],
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")

    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    for role, text in NORMAL_TURNS:
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": role, "content": {"text": text}}}],
        )
    print(f"[boto3] Sent {len(NORMAL_TURNS)} normal events (will be extracted)")

    for role, text in SKIPPED_TURNS:
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": role, "content": {"text": text}}}],
            extractionMode="SKIP",
        )
    print(f"[boto3] Sent {len(SKIPPED_TURNS)} skipped events (extractionMode=SKIP, no extraction)")

    # --- Verify the skipped events are still in short-term memory ---
    events = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=SESSION_ID,
        includePayloads=True,
    )["events"]
    expected_events = len(NORMAL_TURNS) + len(SKIPPED_TURNS)
    print(f"[boto3] Short-term memory has {len(events)} events (all {expected_events} stored)")

    # --- Wait for extraction, then retrieve long-term records ---
    print(f"[boto3] Waiting {EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(EXTRACTION_WAIT_SECONDS)

    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    hits = data.retrieve_memory_records(
        memoryId=memory_id,
        namespace=namespace,
        searchCriteria={"searchQuery": "What do I know about Alex?", "topK": 10},
    )["memoryRecordSummaries"]
    print(f"[boto3] Retrieved {len(hits)} long-term records (only from non-skipped events)")
    for h in hits:
        print(f"  - {h['content']['text']}")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"[boto3] Deleted memory {memory_id}")
    else:
        print(f"[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK ====================================================
# MemoryClient covers both planes we need here: create/delete the resource, and
# create_event/list_events/retrieve_memories for the data plane. This sample stays on
# MemoryClient rather than MemorySessionManager because `extraction_mode` is exposed on
# MemoryClient.create_event and NOT on the session API's add_turns() — and choosing it
# per event is the lesson.
def run_with_sdk(cleanup: bool = False) -> None:
    from bedrock_agentcore.memory import MemoryClient

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"SkipExtractionSdk_{int(time.time())}",
        description="Demonstrates extraction_mode=SKIP (SDK)",
        strategies=[STRATEGY],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    # messages= takes (text, role) tuples — the reverse order of the (role, text)
    # tuples used above, so unpack accordingly. One create_event per turn keeps this
    # directly comparable to the boto3 run above.
    for role, text in NORMAL_TURNS:
        client.create_event(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=SESSION_ID,
            messages=[(text, role)],
        )
    print(f"[sdk] Sent {len(NORMAL_TURNS)} normal events (will be extracted)")

    for role, text in SKIPPED_TURNS:
        client.create_event(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=SESSION_ID,
            messages=[(text, role)],
            extraction_mode="SKIP",
        )
    print(f"[sdk] Sent {len(SKIPPED_TURNS)} skipped events (extraction_mode=SKIP, no extraction)")

    # list_events returns the list directly (not a {"events": ...} dict) and defaults
    # to include_payload=True.
    events = client.list_events(memory_id=memory_id, actor_id=ACTOR_ID, session_id=SESSION_ID)
    expected_events = len(NORMAL_TURNS) + len(SKIPPED_TURNS)
    print(f"[sdk] Short-term memory has {len(events)} events (all {expected_events} stored)")

    print(f"[sdk] Waiting {EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(EXTRACTION_WAIT_SECONDS)

    namespace = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
    hits = client.retrieve_memories(
        memory_id=memory_id,
        namespace=namespace,
        query="What do I know about Alex?",
        top_k=10,
    )
    print(f"[sdk] Retrieved {len(hits)} long-term records (only from non-skipped events)")
    for h in hits:
        print(f"  - {h['content']['text']}")

    if cleanup:
        client.delete_memory_and_wait(memory_id=memory_id)
        print(f"[sdk] Deleted memory {memory_id}")
    else:
        print(f"[sdk] Keeping memory {memory_id} (pass --cleanup to delete)")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--cleanup"]
    cleanup = "--cleanup" in sys.argv[1:]
    surface = args[0] if args else "boto3"
    if surface == "boto3":
        run_with_boto3(cleanup=cleanup)
    elif surface == "sdk":
        run_with_sdk(cleanup=cleanup)
    else:
        print(f"Unknown surface {surface!r}. Use boto3 | sdk.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
