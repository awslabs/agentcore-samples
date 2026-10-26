"""Events and sessions — the building blocks of short-term memory.

What you learn:
    - CreateEvent appends an immutable, timestamped event to a session
    - ListEvents pages through the session in chronological order
    - GetEvent fetches one event in full
    - ListSessions discovers prior sessions for an actor

Two ways to run it, same flow:
    python events-and-sessions.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python events-and-sessions.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

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


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"EventsAndSessions_{int(time.time())}",
        description="Events and sessions tutorial",
        eventExpiryDuration=30,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    session_a = f"session-a-{int(time.time())}"
    session_b = f"session-b-{int(time.time())}"

    for session_id, turns in [
        (
            session_a,
            [
                ("USER", "Book me a flight from Berlin to Lisbon."),
                ("ASSISTANT", "Sure — for which dates?"),
                ("USER", "Next Monday, returning Friday."),
            ],
        ),
        (
            session_b,
            [
                ("USER", "What did I book last week?"),
                ("ASSISTANT", "You booked Berlin to Lisbon, Mon–Fri."),
            ],
        ),
    ]:
        for role, text in turns:
            data.create_event(
                memoryId=memory_id,
                actorId=ACTOR_ID,
                sessionId=session_id,
                # eventTimestamp is REQUIRED by CreateEvent — omitting it raises
                # ParamValidationError. (The SDK surface fills this in for you.)
                eventTimestamp=datetime.now(timezone.utc),
                payload=[{"conversational": {"role": role, "content": {"text": text}}}],
            )

    events = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=session_a,
        includePayloads=True,
    )["events"]
    print(f"[boto3] Session {session_a} has {len(events)} events")

    one = data.get_event(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=session_a,
        eventId=events[0]["eventId"],
    )["event"]
    print(f"[boto3] First event id: {one['eventId']}")

    sessions = data.list_sessions(memoryId=memory_id, actorId=ACTOR_ID)["sessionSummaries"]
    print(f"[boto3] Actor {ACTOR_ID} has {len(sessions)} session(s)")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"[boto3] Deleted memory {memory_id}")
    else:
        print(f"[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
def run_with_sdk(cleanup: bool = False) -> None:
    # MemoryClient owns the control plane (create/delete the resource);
    # MemorySessionManager is data-plane only. A short-term memory needs no
    # extraction strategies, so we create it with strategies=[].
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"EventsAndSessionsSession_{int(time.time())}",
        description="Events and sessions (SDK session API)",
        strategies=[],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    session_a_id = f"session-a-{int(time.time())}"
    session_b_id = f"session-b-{int(time.time())}"

    # Bind one MemorySession per (actor, session). add_turns takes
    # ConversationalMessage objects and writes them as a single event.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session_a = manager.create_memory_session(actor_id=ACTOR_ID, session_id=session_a_id)
    session_b = manager.create_memory_session(actor_id=ACTOR_ID, session_id=session_b_id)

    session_a.add_turns(
        messages=[
            ConversationalMessage("Book me a flight from Berlin to Lisbon.", MessageRole.USER),
            ConversationalMessage("Sure — for which dates?", MessageRole.ASSISTANT),
            ConversationalMessage("Next Monday, returning Friday.", MessageRole.USER),
        ]
    )
    session_b.add_turns(
        messages=[
            ConversationalMessage("What did I book last week?", MessageRole.USER),
            ConversationalMessage("You booked Berlin to Lisbon, Mon–Fri.", MessageRole.ASSISTANT),
        ]
    )

    # list_events returns Event objects (dict-like) in chronological order.
    events = session_a.list_events(include_payload=True)
    print(f"[sdk] Session {session_a_id} has {len(events)} events")

    # get_last_k_turns groups payload messages into logical user/assistant turns.
    turns = session_a.get_last_k_turns(k=5)
    print(f"[sdk] Last {len(turns)} turn(s) in session_a")

    # list_actor_sessions lives on the manager and returns SessionSummary objects.
    sessions = manager.list_actor_sessions(actor_id=ACTOR_ID)
    print(f"[sdk] Actor {ACTOR_ID} has {len(sessions)} session(s)")

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
