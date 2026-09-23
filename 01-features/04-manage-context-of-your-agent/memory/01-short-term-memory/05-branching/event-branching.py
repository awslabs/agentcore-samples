"""Event branching — fork a session for what-if or parallel flows.

What you learn:
    - Set `branch` on CreateEvent to fork from a parent event
    - Filter ListEvents by branch name
    - includeParentBranches=True walks the branch ancestry up to the root

Use cases: exploratory "what if I had said X instead?" turns, parallel
sub-agents that each contribute on a separate branch over a shared parent.

Two ways to run it:
    python event-branching.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python event-branching.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

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
ACTOR_ID = "user-42"


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)
    session_id = f"sess-{int(time.time())}"

    memory_id = control.create_memory(
        name=f"Branching_{int(time.time())}",
        description="Event branching tutorial",
        eventExpiryDuration=30,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    def append(role, text, branch=None):
        kwargs = {
            "memoryId": memory_id,
            "actorId": ACTOR_ID,
            "sessionId": session_id,
            "eventTimestamp": datetime.now(timezone.utc),
            "payload": [{"conversational": {"role": role, "content": {"text": text}}}],
        }
        if branch is not None:
            kwargs["branch"] = branch
        return data.create_event(**kwargs)["event"]

    append("USER", "I'm planning a trip to Lisbon.")
    fork_point = append("ASSISTANT", "When are you thinking of going?")

    autumn = {"name": "autumn", "rootEventId": fork_point["eventId"]}
    append("USER", "October — the weather is mild then.", branch=autumn)
    append("ASSISTANT", "October is great. Here are flights for the second week.", branch=autumn)

    winter = {"name": "winter", "rootEventId": fork_point["eventId"]}
    append("USER", "Actually, what about December for Christmas markets?", branch=winter)
    append("ASSISTANT", "December is busier and pricier. Here's what's available.", branch=winter)

    autumn_only = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=session_id,
        includePayloads=True,
        filter={"branch": {"name": "autumn", "includeParentBranches": False}},
    )["events"]
    print(f"[boto3] Autumn-only events: {len(autumn_only)}")

    winter_full = data.list_events(
        memoryId=memory_id,
        actorId=ACTOR_ID,
        sessionId=session_id,
        includePayloads=True,
        filter={"branch": {"name": "winter", "includeParentBranches": True}},
    )["events"]
    print(f"[boto3] Winter with parents: {len(winter_full)}")

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
        name=f"BranchingSession_{int(time.time())}",
        description="Event branching (SDK session API)",
        strategies=[],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    session_id = f"sess-{int(time.time())}"
    print(f"[sdk] Created memory {memory_id}")

    # Seed a couple of turns on the root branch. add_turns returns an Event
    # (dict-like); we fork from its eventId.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID, session_id=session_id)
    root_event = session.add_turns(
        messages=[
            ConversationalMessage("I'm planning a trip to Lisbon.", MessageRole.USER),
            ConversationalMessage("When are you thinking of going?", MessageRole.ASSISTANT),
        ]
    )

    # fork_conversation creates a new branch rooted at the given event.
    session.fork_conversation(
        root_event_id=root_event["eventId"],
        branch_name="autumn",
        messages=[
            ConversationalMessage("October — the weather is mild then.", MessageRole.USER),
            ConversationalMessage("October is great. Here are flights for the second week.", MessageRole.ASSISTANT),
        ],
    )
    session.fork_conversation(
        root_event_id=root_event["eventId"],
        branch_name="winter",
        messages=[
            ConversationalMessage("Actually, what about December for Christmas markets?", MessageRole.USER),
            ConversationalMessage("December is busier and pricier. Here's what's available.", MessageRole.ASSISTANT),
        ],
    )

    # list_branches returns Branch objects (dict-like) including the implicit "main".
    branches = session.list_branches()
    print(f"[sdk] Branches in session: {[b.get('name') for b in branches]}")

    # The session API exposes branch event filtering through list_events with a
    # branch_name + include_parent_branches flag (there is no list_branch_events
    # method on the session API).
    autumn_only = session.list_events(branch_name="autumn", include_parent_branches=False)
    winter_full = session.list_events(branch_name="winter", include_parent_branches=True)
    print(f"[sdk] Autumn-only events: {len(autumn_only)} | Winter with parents: {len(winter_full)}")

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
