"""Event payload types — conversational, JSON, and blob.

What you learn:
    - An event's `payload` is a list of items, each a union of one of three types
    - `conversational` (role + text) and `json` (structured content under
      `json.content`, max 100 KB per item) are both extracted into long-term memory
    - `blob` (arbitrary data) is stored but never extracted
    - Mixed payloads: several items of different types in one event

The scenario is a car dealership. Most of what a shopper reveals is never said out
loud — cars viewed, filters applied, a financing approval — so those are `json`
events. Extraction strategies are attached even though this sits under short-term
memory, because what extraction does with each type is the whole point.

Two ways to run it:
    python payload-types.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python payload-types.py sdk      # the AgentCore SDK (MemorySessionManager).

The `sdk` path is partial by necessity: `add_turns` takes `ConversationalMessage`
and `BlobMessage`, but there is no JSON message type (checked through
bedrock-agentcore 1.22), so JSON events use boto3 `create_event` even there. It also
needs bedrock-agentcore 1.14+ for `search_long_term_memories(namespace=...)`.

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

REGION = os.getenv("AWS_REGION", "us-east-1")
ACTOR_ID = "customer-456"
SESSION_ID = f"shopping-{int(time.time())}"
FACTS_NAMESPACE = "/customers/{actorId}/facts/"
PREFS_NAMESPACE = "/customers/{actorId}/preferences/"
EXTRACTION_WAIT_SECONDS = 150  # JSON extraction runs the same pipeline as speech; allow margin
JSON_PAYLOAD_LIMIT_BYTES = 100 * 1024

# Field names are part of the input to extraction — call it `view_duration_sec`, not
# `d2`, or the model has nothing to reason about. Keep values locale-neutral too: a
# place name that implies a language can flip the whole record set into that language.
JSON_EVENTS = [
    {
        "event": "car_viewed",
        "car_id": "VH-1044",
        "make": "Honda",
        "model": "Civic",
        "year": 2022,
        "transmission": "automatic",
        "view_duration_sec": 112,
        "price": 21000,
    },
    {
        "event": "search_filter_applied",
        "filters": {
            "body_style": "sedan",
            "min_year": 2021,
            "max_price": 23000,
            "transmission": "automatic",
            "make_preference": ["Honda", "Toyota", "Mazda"],
        },
    },
    {
        "event": "financing_pre_approved",
        "term_months": 48,
        "apr": 5.9,
        "max_amount": 25000,
    },
    {
        "event": "test_drive_scheduled",
        "car_id": "VH-2093",
        "location": "westside-showroom",
        "date": "2026-09-12",
    },
]

USER_TEXT = "Automatic sedan please. I really liked the Corolla."
ASSISTANT_TEXT = "Good choice. You're pre-approved at 5.9% APR — want me to hold it?"

# What the shopper said, what they did, and what the agent replied — one event, so
# extraction sees speech and behaviour together and can corroborate one with the other.
MIXED_PAYLOAD = [
    {"conversational": {"role": "USER", "content": {"text": USER_TEXT}}},
    {
        "json": {
            "content": {
                "event": "car_viewed",
                "car_id": "VH-3310",
                "make": "Toyota",
                "model": "Corolla",
                "year": 2023,
                "view_duration_sec": 185,
            }
        }
    },
    {"conversational": {"role": "ASSISTANT", "content": {"text": ASSISTANT_TEXT}}},
]

BLOB_DATA = {
    "document": "trade-in-appraisal.pdf",
    "encoding": "base64",
    # Truncated base64 of a PDF header, standing in for a real document.
    "bytes": "JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PAo=",  # pragma: allowlist secret
}


def payload_types(payload) -> str:
    """Summarise a payload as its item types, e.g. 'conversational + json'."""
    return " + ".join(key for item in payload or [] for key in item)


def create_dealership_memory(control) -> str:
    """Create a memory with semantic + user-preference strategies, wait for ACTIVE."""
    memory_id = control.create_memory(
        name=f"PayloadTypes_{int(time.time())}",
        description="Event payload types tutorial",
        eventExpiryDuration=30,
        memoryStrategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "ShopperFacts",
                    "description": "Standalone facts about the shopper",
                    "namespaces": [FACTS_NAMESPACE],
                }
            },
            {
                "userPreferenceMemoryStrategy": {
                    "name": "ShopperPreferences",
                    "description": "Stable vehicle preferences",
                    "namespaces": [PREFS_NAMESPACE],
                }
            },
        ],
    )["memory"]["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)
    return memory_id


def write_json_events(data, memory_id: str) -> None:
    """Write the four JSON-only events. Structured content goes under json.content."""
    for content in JSON_EVENTS:
        # Check the size limit locally rather than as a ValidationException later.
        size = len(json.dumps(content).encode("utf-8"))
        if size > JSON_PAYLOAD_LIMIT_BYTES:
            raise ValueError(f"json payload is {size} bytes, over the {JSON_PAYLOAD_LIMIT_BYTES}-byte limit")
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"json": {"content": content}}],
        )
    print(f"  wrote {len(JSON_EVENTS)} json-only events")


def show_records(retrieve, prefix: str) -> None:
    """Poll both namespaces until records appear, then print them.

    `retrieve(namespace, query)` is the surface-specific search call.
    """
    facts_ns = FACTS_NAMESPACE.format(actorId=ACTOR_ID)
    prefs_ns = PREFS_NAMESPACE.format(actorId=ACTOR_ID)
    print(f"{prefix} Polling up to {EXTRACTION_WAIT_SECONDS}s for extraction...")

    deadline = time.time() + EXTRACTION_WAIT_SECONDS
    while True:
        facts = retrieve(facts_ns, "what car is the customer interested in, and what financing do they have?")
        prefs = retrieve(prefs_ns, "what body style, transmission, and brands does the customer prefer?")
        if (facts and prefs) or time.time() >= deadline:
            break
        time.sleep(10)
    if not facts and not prefs:
        print(f"{prefix} No records after {EXTRACTION_WAIT_SECONDS}s — extraction may still be running.")

    for namespace, records in ((facts_ns, facts), (prefs_ns, prefs)):
        print(f"\n{prefix} Records in {namespace}:")
        for record in records:
            print(f"  - {record['content']['text']}")
    print(f"\n{prefix} All of it came from the json and conversational items; none from the blob.")


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = create_dealership_memory(control)
    print(f"[boto3] Created memory {memory_id}")

    write_json_events(data, memory_id)
    for label, payload in (("mixed conversational + json", MIXED_PAYLOAD), ("blob", [{"blob": BLOB_DATA}])):
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=payload,
        )
        print(f"  wrote 1 {label} event")

    # All three types round-trip through short-term memory unchanged.
    events = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=SESSION_ID,
        includePayloads=True,
    )["events"]
    print(f"\n[boto3] Session {SESSION_ID} has {len(events)} events:")
    for event in events:
        print(f"  {event['eventId']}  payload: {payload_types(event.get('payload'))}")

    def retrieve(namespace: str, query: str):
        return data.retrieve_memory_records(
            memoryId=memory_id,
            namespace=namespace,
            searchCriteria={"searchQuery": query, "topK": 10},
        )["memoryRecordSummaries"]

    show_records(retrieve, "[boto3]")

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
    import boto3
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import BlobMessage, ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"PayloadTypesSession_{int(time.time())}",
        description="Event payload types (SDK session API)",
        strategies=[
            {
                "semanticMemoryStrategy": {
                    "name": "ShopperFacts",
                    "description": "Standalone facts about the shopper",
                    # Current field is namespaceTemplates (namespaces is deprecated).
                    "namespaceTemplates": [FACTS_NAMESPACE],
                }
            },
            {
                "userPreferenceMemoryStrategy": {
                    "name": "ShopperPreferences",
                    "description": "Stable vehicle preferences",
                    "namespaceTemplates": [PREFS_NAMESPACE],
                }
            },
        ],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)

    print("[sdk] add_turns has no JSON type; writing json events with boto3 create_event")
    write_json_events(boto3.client("bedrock-agentcore", region_name=REGION), memory_id)

    # add_turns maps the whole message list to one create_event, so mixing types in
    # a single call produces a single mixed payload — here conversational + blob.
    session.add_turns(
        messages=[
            ConversationalMessage(USER_TEXT, MessageRole.USER),
            BlobMessage(BLOB_DATA),
            ConversationalMessage(ASSISTANT_TEXT, MessageRole.ASSISTANT),
        ]
    )
    print("  wrote 1 mixed conversational + blob event via add_turns")

    events = session.list_events(include_payload=True)
    print(f"\n[sdk] Session {SESSION_ID} has {len(events)} events:")
    for event in events:
        print(f"  {event['eventId']}  payload: {payload_types(event.get('payload'))}")

    # get_last_k_turns rebuilds turns from conversational items only, so rehydrating a
    # prompt this way silently drops every json event and the blob item above.
    turns = session.get_last_k_turns(k=10)
    print(
        f"\n[sdk] get_last_k_turns returned {len(turns)} turn(s) / "
        f"{sum(len(turn) for turn in turns)} message(s) — json and blob items are dropped"
    )

    def retrieve(namespace: str, query: str):
        # namespace= is exact match; namespace_prefix= is deprecated.
        return session.search_long_term_memories(query=query, namespace=namespace, top_k=10)

    show_records(retrieve, "[sdk]")

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
