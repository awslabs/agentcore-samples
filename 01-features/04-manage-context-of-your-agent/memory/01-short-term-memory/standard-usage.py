"""Standard usage of AgentCore short-term memory.

The canonical short-term flow:
    1. create a memory resource
    2. wait until it is ACTIVE
    3. append a few events to a session
    4. list the events back to reload context
    5. fetch one event in full
    6. tear down

The same flow is shown two ways. Use boto3 to see the raw API with no SDK, or
the AgentCore SDK (MemorySessionManager) for the ergonomic helpers you'd use in
real code.

Two ways to run it:
    python standard-usage.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python standard-usage.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

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
SESSION_ID = f"sess-{int(time.time())}"


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"StmStandard_{int(time.time())}",
        description="Short-term memory standard usage (boto3)",
        eventExpiryDuration=30,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")

    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    for role, text in [
        ("USER", "Hi, I'm Alex. I prefer Python over Java."),
        ("ASSISTANT", "Got it, Alex — I'll lean toward Python in examples."),
        ("USER", "What did I tell you about my language preference?"),
    ]:
        data.create_event(
            memoryId=memory_id,
            actorId=ACTOR_ID,
            sessionId=SESSION_ID,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": role, "content": {"text": text}}}],
        )

    events = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=SESSION_ID,
        includePayloads=True,
    )["events"]
    print(f"[boto3] Session {SESSION_ID} has {len(events)} events")

    first = data.get_event(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=SESSION_ID,
        eventId=events[0]["eventId"],
    )["event"]
    print(f"[boto3] First event payload: {first['payload']}")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"[boto3] Deleted memory {memory_id}")
    else:
        print(f"[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
def run_with_sdk(cleanup: bool = False) -> None:
    # MemoryClient owns the control plane (create/delete); MemorySessionManager
    # is data-plane only. No extraction strategies for short-term memory.
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"StmStandardSession_{int(time.time())}",
        description="Short-term memory standard usage (SDK session API)",
        strategies=[],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    # Bind a session, then write all turns in one add_turns call. add_turns
    # takes ConversationalMessage objects and maps to a single create_event.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=SESSION_ID)
    session.add_turns(
        messages=[
            ConversationalMessage("Hi, I'm Alex. I prefer Python over Java.", MessageRole.USER),
            ConversationalMessage("Got it, Alex — I'll lean toward Python in examples.", MessageRole.ASSISTANT),
            ConversationalMessage("What did I tell you about my language preference?", MessageRole.USER),
        ]
    )

    # get_last_k_turns is the session API's idiomatic way to reload context.
    # Each turn is a list of EventMessage objects (dict-like): role + content.text.
    turns = session.get_last_k_turns(k=5)
    print(f"[sdk] Session {SESSION_ID} has {len(turns)} turns")
    for turn in turns:
        for msg in turn:
            print(f"  {msg['role']}: {msg['content']['text']}")

    if cleanup:
        client.delete_memory_and_wait(memory_id=memory_id)
        print(f"[sdk] Deleted memory {memory_id}")
    else:
        print(f"[sdk] Keeping memory {memory_id} (pass --cleanup to delete)")


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
