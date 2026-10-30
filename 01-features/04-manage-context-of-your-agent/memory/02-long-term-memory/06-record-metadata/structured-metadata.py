"""Structured metadata on memory records.

What you learn:
    - Attach metadata when calling BatchCreateMemoryRecords
    - Filter retrieval with metadataFilters on RetrieveMemoryRecords
    - Use indexedKeys on CreateMemory to declare filterable keys

Use record metadata for hard constraints (region, tier, source, language)
that should be enforced at the index, not in the LLM prompt.

Two ways to run it:
    python structured-metadata.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python structured-metadata.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

Add `--cleanup` to delete the memory resource at the end. By default the
memory is kept so you can inspect it; the script prints the memoryId.

The `sdk` run declares filterable keys with `create_memory_and_wait(indexed_keys=...)`
and builds metadata filters with the typed `MemoryMetadataFilter` builder instead of
hand-written boto3 dicts. It needs bedrock-agentcore 1.14 or newer, because it searches
with `search_long_term_memories(namespace=...)`. Older versions only accept the
deprecated `namespace_prefix=`.

Prerequisites:
    pip install boto3 bedrock-agentcore
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
"""

import os
import sys
import time
import uuid

REGION = os.getenv("AWS_REGION", "us-east-1")
ACTOR_ID = "tenant-acme"
NAMESPACE = f"/tenants/{ACTOR_ID}/notes/"
SEARCHABLE_WAIT_SECONDS = 100  # polling budget; records became searchable at 75-97s across runs
EXPECTED_EU_RECORDS = 2  # of the 3 records written below, 2 carry region=EU


def _search_until_visible(
    op, expected: int = EXPECTED_EU_RECORDS, *, max_wait: int = SEARCHABLE_WAIT_SECONDS, poll: int = 10
) -> list:
    """Call `op()` until it returns `expected` records, or the deadline passes.

    Directly-written records are eventually consistent. Measured against a live
    account over three runs they became searchable at 75s, 86s and 97s after
    BatchCreate, so any single fixed sleep is a guess that eventually loses.

    This waits for the expected COUNT, not merely for a non-empty result: the index
    populates record by record, so a run that stops at the first hit can report 1 of
    the 2 EU records and look like the filter dropped one. The count is deterministic
    here because the records are written directly rather than extracted.
    """
    deadline = time.time() + max_wait
    while True:
        hits = op()
        if len(hits) >= expected or time.time() >= deadline:
            return hits
        time.sleep(poll)


def _records() -> list[dict]:
    return [
        {
            "requestIdentifier": "rec-eu-premium",
            "content": {"text": "Acme prefers GDPR-compliant data residency."},
            "namespaces": [NAMESPACE],
            "timestamp": str(int(time.time())),
            "metadata": {
                "region": {"stringValue": "EU"},
                "tier": {"stringValue": "premium"},
            },
        },
        {
            "requestIdentifier": "rec-us-basic",
            "content": {"text": "Acme has a US billing address."},
            "namespaces": [NAMESPACE],
            "timestamp": str(int(time.time())),
            "metadata": {
                "region": {"stringValue": "US"},
                "tier": {"stringValue": "basic"},
            },
        },
        {
            "requestIdentifier": "rec-eu-basic",
            "content": {"text": "Acme support tickets are routed to the Berlin team."},
            "namespaces": [NAMESPACE],
            "timestamp": str(int(time.time())),
            "metadata": {
                "region": {"stringValue": "EU"},
                "tier": {"stringValue": "basic"},
            },
        },
    ]


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"RecordMetadata_{int(time.time())}",
        description="Structured metadata (boto3)",
        eventExpiryDuration=30,
        indexedKeys=[
            {"key": "region", "type": "STRING"},
            {"key": "tier", "type": "STRING"},
        ],
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    resp = data.batch_create_memory_records(memoryId=memory_id, records=_records())
    print(f"[boto3] Created {len(resp.get('successfulRecords', []))} records")

    # Directly-written records are eventually consistent — poll until both EU records
    # are searchable instead of sleeping a fixed amount.
    print(f"[boto3] Polling up to {SEARCHABLE_WAIT_SECONDS}s for records to become searchable...")
    hits = _search_until_visible(
        lambda: data.retrieve_memory_records(
            memoryId=memory_id,
            namespace=NAMESPACE,
            searchCriteria={
                "searchQuery": "Acme",
                "topK": 10,
                "metadataFilters": [
                    {
                        "left": {"metadataKey": "region"},
                        "operator": "EQUALS_TO",
                        "right": {"metadataValue": {"stringValue": "EU"}},
                    }
                ],
            },
        )["memoryRecordSummaries"]
    )
    print(f"\n[boto3] EU-only results ({len(hits)}):")
    for h in hits:
        print(f"  - {h['content']['text']} | meta={h.get('metadata')}")
    if len(hits) < EXPECTED_EU_RECORDS:
        print(
            f"[boto3] Expected {EXPECTED_EU_RECORDS} EU records, got {len(hits)} after "
            f"{SEARCHABLE_WAIT_SECONDS}s — still propagating, so the metadata filter cannot be "
            "fully demonstrated from this run."
        )

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\n[boto3] Deleted memory {memory_id}")
    else:
        print(f"\n[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
# MemoryClient owns the control plane; MemorySessionManager is data-plane only.
# This run uses two SDK ergonomics:
#   1) create_memory_and_wait(indexed_keys=[IndexedKey.build(...)]) declares the
#      filterable keys without dropping to the raw control-plane client.
#   2) MemoryMetadataFilter.build_expression(...) builds the metadata filter as a
#      typed object instead of a hand-written boto3 dict, then we hand the list to
#      session.search_long_term_memories(metadata_filters=[...]).
def run_with_sdk(cleanup: bool = False) -> None:
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager
    from bedrock_agentcore.memory.models.filters import (
        IndexedKey,
        MemoryMetadataFilter,
        MemoryRecordLeftExpression,
        MemoryRecordOperatorType,
        MemoryRecordRightExpression,
        MetadataValueType,
    )

    client = MemoryClient(region_name=REGION)
    # No extraction strategy: records are written directly, so indexed_keys is the
    # only thing we need at create time to make region/tier filterable.
    memory = client.create_memory_and_wait(
        name=f"RecordMetadataSession_{int(time.time())}",
        description="Structured metadata (SDK session API)",
        strategies=[],
        event_expiry_days=30,
        indexed_keys=[
            IndexedKey.build("region", MetadataValueType.STRING),
            IndexedKey.build("tier", MetadataValueType.STRING),
        ],
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    # batch_create_memory_records is forwarded by MemorySessionManager (data-plane
    # allowlist) — NOT by the per-session MemorySession — and the forward is a thin
    # boto3 passthrough, so memoryId must be passed explicitly (it is not injected
    # from session binding). Nested record dicts stay camelCase (snake_case
    # conversion only touches top-level kwargs, not values).
    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)
    session = manager.create_memory_session(actor_id=ACTOR_ID)
    resp = manager.batch_create_memory_records(memoryId=memory_id, records=_records())
    print(
        f"[sdk] Created {len(resp.get('successfulRecords', []))} records ({len(resp.get('failedRecords', []))} failed)"
    )

    # Same EU-only filter as boto3, but built via the typed expression helper.
    region_eu = MemoryMetadataFilter.build_expression(
        MemoryRecordLeftExpression.build("region"),
        MemoryRecordOperatorType.EQUALS_TO,
        MemoryRecordRightExpression.build_string("EU"),
    )
    # Same eventual-consistency poll as the boto3 path above.
    print(f"[sdk] Polling up to {SEARCHABLE_WAIT_SECONDS}s for records to become searchable...")
    hits = _search_until_visible(
        lambda: session.search_long_term_memories(
            query="Acme",
            namespace=NAMESPACE,
            top_k=10,
            metadata_filters=[region_eu],
        )
    )
    print(f"\n[sdk] EU-only results ({len(hits)}):")
    for h in hits:
        # Each hit is a MemoryRecord (dict-like): content.text + metadata.
        print(f"  - {h['content']['text']} | meta={h.get('metadata')}")
    if len(hits) < EXPECTED_EU_RECORDS:
        print(
            f"[sdk] Expected {EXPECTED_EU_RECORDS} EU records, got {len(hits)} after "
            f"{SEARCHABLE_WAIT_SECONDS}s — still propagating, so the metadata filter cannot be "
            "fully demonstrated from this run."
        )

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
