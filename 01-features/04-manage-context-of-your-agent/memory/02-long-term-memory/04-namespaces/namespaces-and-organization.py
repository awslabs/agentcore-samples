"""Namespaces — organising long-term memory records.

A namespace is the path a long-term record is written to. You set it per strategy
as a *template*, and the built-in variables resolve from each event:

    {actorId}    {sessionId}    {memoryStrategyId}

Three strategies share one actor-first tree, so everything about a user sits
under one prefix and each kind of record still has its own subtree:

    /users/{actorId}/facts/                  <- semantic
    /users/{actorId}/preferences/            <- user preference
    /users/{actorId}/sessions/{sessionId}/   <- summary

The queries below then read it at three depths: exact (`namespace=`) for one
strategy, and `namespacePath=` for one user or for all of them. A type-first tree
(`/facts/{actorId}/`) would invert which of those is cheap — see README.md.

Custom variables (tenant, agent, region) are in flexible-namespaces.py.

Two ways to run it:
    python namespaces-and-organization.py boto3    # the raw AWS API. Shows exactly what's on the wire.
    python namespaces-and-organization.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

The `sdk` path needs bedrock-agentcore 1.14 or newer (`search_long_term_memories(namespace=...)`).
Add `--cleanup` to delete the memory resource at the end. The same flow via
the AWS CLI is in the README.

Prerequisites:
    pip install boto3 "bedrock-agentcore>=1.14"
    export AWS_REGION=us-east-1   # any AgentCore-supported region
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone

REGION = os.getenv("AWS_REGION", "us-east-1")
EXTRACTION_WAIT_SECONDS = 120  # extraction is async; poll, don't assume

# Every template starts *and* ends with "/" — the trailing slash is what keeps
# /users/alice/ from also matching /users/alice2/. A summary template must contain
# {sessionId}: summaries are generated and maintained per session.
STRATEGIES = [
    {"semanticMemoryStrategy": {"name": "Facts", "namespaceTemplates": ["/users/{actorId}/facts/"]}},
    {
        "userPreferenceMemoryStrategy": {
            "name": "Preferences",
            "namespaceTemplates": ["/users/{actorId}/preferences/"],
        }
    },
    {
        "summaryMemoryStrategy": {
            "name": "Sessions",
            "namespaceTemplates": ["/users/{actorId}/sessions/{sessionId}/"],
        }
    },
]

# Two actors, one session each. Each turn carries a fact and a preference, so
# more than one strategy has something to extract.
CONVERSATIONS = [
    (
        "alice",
        [
            ("I'm Alice, I lead the data platform team in Berlin.", "Good to know."),
            ("Always answer in metric units, and keep it short.", "Understood."),
        ],
    ),
    (
        "bob",
        [
            ("I'm Bob, a backend engineer in Seattle.", "Nice to meet you."),
            ("I prefer Python examples over Java ones.", "Python it is."),
        ],
    ),
]

# The three scopes this tree supports.
QUERIES = [
    ("Exact — alice's facts", "what does alice work on", {"namespace": "/users/alice/facts/"}),
    ("Prefix — everything about alice", "alice", {"namespacePath": "/users/alice/"}),
    ("Prefix — every user", "who are these users", {"namespacePath": "/users/"}),
]


def _print_hits(prefix: str, label: str, scope: dict, hits: list) -> None:
    print(f"\n[{prefix}] {label} — {next(iter(scope.values()))} ({len(hits)}):")
    for h in hits:
        # `namespaces` is the *resolved* path. Read it rather than assuming.
        print(f"  - [{','.join(h.get('namespaces', []))}] {h['content']['text'][:80]}")


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"Namespaces_{int(time.time())}",
        description="Namespaces tutorial (boto3)",
        eventExpiryDuration=30,
        memoryStrategies=STRATEGIES,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while control.get_memory(memoryId=memory_id)["memory"]["status"] != "ACTIVE":
        if time.time() > deadline:
            raise TimeoutError(f"{memory_id} never became ACTIVE")
        time.sleep(5)

    # actorId and sessionId on the event are what the templates substitute.
    for actor_id, turns in CONVERSATIONS:
        session_id = f"{actor_id}-{int(time.time())}"
        for user_text, assistant_text in turns:
            data.create_event(
                memoryId=memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    {"conversational": {"role": "USER", "content": {"text": user_text}}},
                    {"conversational": {"role": "ASSISTANT", "content": {"text": assistant_text}}},
                ],
            )
        print(f"[boto3] Wrote {len(turns)} turns for {actor_id} in session {session_id}")

    # Latency varies, so poll the broadest query rather than sleeping a fixed amount.
    print(f"[boto3] Polling up to {EXTRACTION_WAIT_SECONDS}s for extraction...")
    deadline = time.time() + EXTRACTION_WAIT_SECONDS
    _, probe_query, probe_scope = QUERIES[-1]
    while time.time() < deadline:
        if data.retrieve_memory_records(
            memoryId=memory_id,
            searchCriteria={"searchQuery": probe_query, "topK": 20},
            **probe_scope,
        )["memoryRecordSummaries"]:
            break
        time.sleep(10)

    for label, query, scope in QUERIES:
        hits = data.retrieve_memory_records(
            memoryId=memory_id,
            searchCriteria={"searchQuery": query, "topK": 20},
            **scope,
        )["memoryRecordSummaries"]
        _print_hits("boto3", label, scope, hits)

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\n[boto3] Deleted memory {memory_id}")
    else:
        print(f"\n[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
def run_with_sdk(cleanup: bool = False) -> None:
    # MemoryClient owns the control plane (create/delete the resource);
    # MemorySessionManager is data-plane only.
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

    client = MemoryClient(region_name=REGION)
    memory_id = client.create_memory_and_wait(
        name=f"NamespacesSdk_{int(time.time())}",
        description="Namespaces tutorial (SDK)",
        strategies=STRATEGIES,
        event_expiry_days=30,
    )["id"]
    print(f"[sdk] Created memory {memory_id}")

    # One MemorySession per actor: the session carries the actorId and sessionId
    # the templates resolve from.
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    for actor_id, turns in CONVERSATIONS:
        session = manager.create_memory_session(actor_id=actor_id, session_id=f"{actor_id}-{int(time.time())}")
        for user_text, assistant_text in turns:
            session.add_turns(
                messages=[
                    ConversationalMessage(user_text, MessageRole.USER),
                    ConversationalMessage(assistant_text, MessageRole.ASSISTANT),
                ]
            )
        print(f"[sdk] Wrote {len(turns)} turns for {actor_id} in session {session.session_id}")
    print(f"[sdk] Waiting {EXTRACTION_WAIT_SECONDS}s for extraction...")
    time.sleep(EXTRACTION_WAIT_SECONDS)

    # Retrieval is scoped by the namespace argument, not by the session's bound
    # actor, so one session can run all three scopes.
    query_session = manager.create_memory_session(actor_id=CONVERSATIONS[0][0])
    for label, query, scope in QUERIES:
        kwargs = (
            {"namespace": scope["namespace"]} if "namespace" in scope else {"namespace_path": scope["namespacePath"]}
        )
        hits = query_session.search_long_term_memories(query=query, top_k=20, **kwargs)
        _print_hits("sdk", label, scope, hits)

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
