"""Actor and session isolation.

What you learn:
    - A single memory resource serves many actors
    - Events are scoped by (actorId, sessionId) — no cross-actor leakage
    - ListEvents under one actor never returns another actor's events
    - The `sdk` run ASSERTS isolation (negative checks: one actor's
      events/sessions never contain the other's) rather than only narrating it

Two ways to run it:
    python actor-session-isolation.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python actor-session-isolation.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

The `sdk` path needs bedrock-agentcore 1.14 or newer, because it searches with
`search_long_term_memories(namespace=...)`. Older versions only accept the deprecated
`namespace_prefix=`.

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


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"ActorIsolation_{int(time.time())}",
        description="Actor/session isolation tutorial",
        eventExpiryDuration=30,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    alice_session = f"alice-{int(time.time())}"
    bob_session = f"bob-{int(time.time())}"

    def write(actor, session, role, text):
        data.create_event(
            memoryId=memory_id,
            actorId=actor,
            sessionId=session,
            eventTimestamp=datetime.now(timezone.utc),
            payload=[{"conversational": {"role": role, "content": {"text": text}}}],
        )

    write("alice", alice_session, "USER", "I'm flying to Tokyo next week.")
    write("alice", alice_session, "ASSISTANT", "Got it.")
    write("bob", bob_session, "USER", "Remind me about my dentist appointment.")
    write("bob", bob_session, "ASSISTANT", "Friday at 3pm.")

    alice_events = data.list_events(memoryId=memory_id, actorId="alice", sessionId=alice_session, includePayloads=True)[
        "events"
    ]
    bob_events = data.list_events(memoryId=memory_id, actorId="bob", sessionId=bob_session, includePayloads=True)[
        "events"
    ]
    print(f"[boto3] Alice: {len(alice_events)} events | Bob: {len(bob_events)} events")

    alice_sessions = data.list_sessions(memoryId=memory_id, actorId="alice")["sessionSummaries"]
    bob_sessions = data.list_sessions(memoryId=memory_id, actorId="bob")["sessionSummaries"]
    print(f"[boto3] Alice sessions: {[s['sessionId'] for s in alice_sessions]}")
    print(f"[boto3] Bob sessions:   {[s['sessionId'] for s in bob_sessions]}")

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
        name=f"ActorIsolationSession_{int(time.time())}",
        description="Actor/session isolation (SDK session API)",
        strategies=[],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    alice_session_id = f"alice-{int(time.time())}"
    bob_session_id = f"bob-{int(time.time())}"

    # One memory resource, two actors. Each MemorySession is bound to its own
    # (actor, session) pair, so events never cross between actors.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    alice = manager.create_memory_session(actor_id="alice", session_id=alice_session_id)
    bob = manager.create_memory_session(actor_id="bob", session_id=bob_session_id)

    alice.add_turns(
        messages=[
            ConversationalMessage("I'm flying to Tokyo next week.", MessageRole.USER),
            ConversationalMessage("Got it.", MessageRole.ASSISTANT),
        ]
    )
    bob.add_turns(
        messages=[
            ConversationalMessage("Remind me about my dentist appointment.", MessageRole.USER),
            ConversationalMessage("Friday at 3pm.", MessageRole.ASSISTANT),
        ]
    )

    # Each session only ever sees its own actor's events.
    alice_events = alice.list_events()
    bob_events = bob.list_events()
    print(f"[sdk] Alice: {len(alice_events)} events | Bob: {len(bob_events)} events")

    # list_actor_sessions is scoped to one actor: no cross-actor leakage.
    alice_sessions = manager.list_actor_sessions(actor_id="alice")
    bob_sessions = manager.list_actor_sessions(actor_id="bob")
    print(f"[sdk] Alice sessions: {[s['sessionId'] for s in alice_sessions]}")
    print(f"[sdk] Bob sessions:   {[s['sessionId'] for s in bob_sessions]}")

    # --- Prove isolation (negative assertions), don't just claim it -----------
    # Narrating "no cross-actor leakage" isn't enough — assert it. Each Event is
    # a DictWrapper over the raw event; conversational text lives at
    # payload[].conversational.content.text (see add_turns, session.py:497).
    def _texts(events) -> list:
        out = []
        for ev in events:
            for item in ev.get("payload", []) or []:
                conv = item.get("conversational") if isinstance(item, dict) else None
                if conv:
                    out.append(conv.get("content", {}).get("text", ""))
        return out

    alice_texts = _texts(alice_events)
    bob_texts = _texts(bob_events)
    alice_session_ids = {s["sessionId"] for s in alice_sessions}
    bob_session_ids = {s["sessionId"] for s in bob_sessions}

    # 1. Each actor sees only its own turns.
    assert len(alice_events) > 0 and len(bob_events) > 0, "both actors should have events"
    # 2. Bob's content never appears under Alice, and vice versa.
    assert not any("dentist" in t for t in alice_texts), "LEAK: Bob's content surfaced under Alice"
    assert not any("Tokyo" in t for t in bob_texts), "LEAK: Alice's content surfaced under Bob"
    # 3. Session listings are disjoint — neither actor's session appears under the other.
    assert bob_session_id not in alice_session_ids, "LEAK: Bob's session listed under Alice"
    assert alice_session_id not in bob_session_ids, "LEAK: Alice's session listed under Bob"
    assert alice_session_ids.isdisjoint(bob_session_ids), "LEAK: actors share a session id"
    print("[sdk] ✅ Isolation verified: no cross-actor event or session leakage.")

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
