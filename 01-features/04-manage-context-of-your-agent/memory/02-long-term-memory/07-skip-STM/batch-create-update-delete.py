"""Batch CRUD on memory records — bypassing the extraction pipeline.

What you learn:
    - BatchCreateMemoryRecords to insert records you've extracted yourself
    - BatchUpdateMemoryRecords to overwrite content of existing records
    - BatchDeleteMemoryRecords to remove records by id

These are the data-plane CRUD APIs. Use them when you've extracted
records outside AgentCore (e.g. via a self-managed strategy) or for
back-fills, migrations, and admin tooling.

Each call accepts up to 100 records and reports per-record success/failure.

Eventual consistency: a record returned as SUCCEEDED by BatchCreate is NOT
immediately readable/updatable. This is expected for directly-written records, and
it shows up in two different ways, so this script handles it in two different ways:

  - BatchUpdate/BatchDelete can raise ResourceNotFoundException until the record
    propagates. Retry on that error (`_retry_until_propagated`).
  - ListMemoryRecords does NOT raise — it succeeds and returns an empty page, and
    it lags the furthest behind. Measured against a live account, update and delete
    both succeeded on the first attempt while the surviving records took ~87s to
    become listable. Retrying on an exception cannot help here, so poll until the
    expected record count appears (`_list_until_visible`).

The real-world pattern — shown below — is to wait for the dependent operation to
reflect the write, rather than assume it is available right after create.

Two ways to run it:
    python batch-create-update-delete.py boto3    # the raw AWS API, no SDK. Shows exactly what's on the wire.
    python batch-create-update-delete.py sdk      # the AgentCore SDK (MemorySessionManager). The recommended way.

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

from botocore.exceptions import ClientError

REGION = os.getenv("AWS_REGION", "us-east-1")
ACTOR_ID = "user-alex"
NAMESPACE = f"/users/{ACTOR_ID}/notes/"
PROPAGATION_WAIT_SECONDS = 120  # polling budget; records became listable ~87s after the delete
EXPECTED_REMAINING = 2  # each surface below creates 3 records and deletes 1


def _list_until_visible(
    op, expected: int = EXPECTED_REMAINING, *, max_wait: int = PROPAGATION_WAIT_SECONDS, poll: int = 10
) -> list:
    """Call `op()` until it returns `expected` records, or the deadline passes.

    List lags further behind BatchCreate than update/delete do: measured against a
    live account, the update and delete below both succeeded on the first attempt
    while the surviving records took ~87s to become listable. A bare List straight
    after the delete therefore reports zero records, which contradicts the
    create/update/delete arithmetic the script just printed.

    This needs its own helper rather than `_retry_until_propagated`: there is no
    exception to catch here, because an unpropagated List succeeds and simply
    returns an empty page. Waiting for the expected count also rides out the
    reverse case, where the deleted record is still visible.
    """
    deadline = time.time() + max_wait
    while True:
        records = op()
        if len(records) == expected or time.time() >= deadline:
            return records
        time.sleep(poll)


def _retry_until_propagated(op, *, max_wait: int = 150, poll: int = 10):
    """Call `op()` and retry while the record is still propagating.

    Directly-written records are eventually consistent, so a dependent op
    (BatchUpdate/Delete/List) on a just-created record may raise
    ResourceNotFoundException until propagation completes. We retry ONLY that
    transient error, with a deadline, and re-raise anything else immediately.
    Returns op()'s result; raises the last ResourceNotFoundException on timeout.

    This retries the ACTUAL dependent op, not a List probe: "shows up in List"
    and "is updatable" are not the same moment, so polling List first can still
    leave the update racing ahead of propagation.
    """
    deadline = time.time() + max_wait
    last = None
    while time.time() < deadline:
        try:
            return op()
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise  # deterministic error — surface it
            last = e
            time.sleep(poll)
    raise last


# === boto3 ============================================================
def run_with_boto3(cleanup: bool = False) -> None:
    import boto3

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    data = boto3.client("bedrock-agentcore", region_name=REGION)

    memory_id = control.create_memory(
        name=f"BatchCRUD_{int(time.time())}",
        description="Batch APIs tutorial (boto3)",
        eventExpiryDuration=30,
    )["memory"]["id"]
    print(f"[boto3] Created memory {memory_id}")
    deadline = time.time() + 300
    while time.time() < deadline:
        if control.get_memory(memoryId=memory_id)["memory"]["status"] == "ACTIVE":
            break
        time.sleep(5)

    create_resp = data.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": "note-lang",
                "namespaces": [NAMESPACE],
                # timestamp is a datetime-typed field — pass a real datetime, not a string.
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex prefers Python over Java."},
            },
            {
                "requestIdentifier": "note-city",
                "namespaces": [NAMESPACE],
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex is based in Berlin."},
            },
            {
                "requestIdentifier": "note-allergy",
                "namespaces": [NAMESPACE],
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex is allergic to peanuts."},
            },
        ],
    )
    successes = create_resp.get("successfulRecords", [])
    print(f"[boto3] Created {len(successes)} ({len(create_resp.get('failedRecords', []))} failed)")
    record_ids = {r["requestIdentifier"]: r["memoryRecordId"] for r in successes}

    # The just-created records are eventually consistent — retry the dependent
    # update/delete until they propagate (BatchUpdateMemoryRecords requires
    # memoryRecordId AND timestamp).
    update_resp = _retry_until_propagated(
        lambda: data.batch_update_memory_records(
            memoryId=memory_id,
            records=[
                {
                    "memoryRecordId": record_ids["note-lang"],
                    "timestamp": datetime.now(timezone.utc),
                    "content": {"text": "Alex prefers Python and writes Rust for hot paths."},
                }
            ],
        )
    )
    print(f"[boto3] Updated {len(update_resp.get('successfulRecords', []))}")

    delete_resp = _retry_until_propagated(
        lambda: data.batch_delete_memory_records(
            memoryId=memory_id,
            records=[{"memoryRecordId": record_ids["note-allergy"]}],
        )
    )
    print(f"[boto3] Deleted {len(delete_resp.get('successfulRecords', []))}")

    # List is eventually consistent too, so poll for the expected count instead of
    # listing once and reporting whatever happens to be visible (which right after
    # the delete is nothing).
    print(f"\n[boto3] Polling up to {PROPAGATION_WAIT_SECONDS}s for records to become listable...")
    remaining = _list_until_visible(
        lambda: data.list_memory_records(memoryId=memory_id, namespace=NAMESPACE)["memoryRecordSummaries"]
    )
    print(f"\n[boto3] Remaining ({len(remaining)}):")
    for r in remaining:
        print(f"  - {r['content']['text']}")
    if len(remaining) != EXPECTED_REMAINING:
        print(f"[boto3] Expected {EXPECTED_REMAINING} records after {PROPAGATION_WAIT_SECONDS}s — still propagating.")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\n[boto3] Deleted memory {memory_id}")
    else:
        print(f"\n[boto3] Keeping memory {memory_id} (pass --cleanup to delete)")


# === AgentCore SDK — high-level MemorySessionManager =================
# MemoryClient owns the control plane (create/delete the resource);
# MemorySessionManager is data-plane only. The three batch_* calls are forwarded
# by MemorySessionManager via __getattr__ (data-plane allowlist), so we can run
# the whole create/update/delete cycle through the session manager. The nested
# record dicts stay camelCase: snake_case conversion only rewrites top-level
# kwargs (records=, memory_id=), never the dict values.
def run_with_sdk(cleanup: bool = False) -> None:
    from bedrock_agentcore.memory import MemoryClient, MemorySessionManager

    client = MemoryClient(region_name=REGION)
    memory = client.create_memory_and_wait(
        name=f"BatchCRUDSession_{int(time.time())}",
        description="Batch APIs tutorial (SDK session API)",
        strategies=[],
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    print(f"[sdk] Created memory {memory_id}")

    manager = MemorySessionManager(memory_id=memory_id, region_name=REGION)

    # batch_* are forwarded to boto3 by MemorySessionManager as thin passthroughs:
    # memoryId is NOT injected from the manager's binding, so pass it explicitly.
    # Each batch accepts up to 100 records; keep this demo well under the cap.
    create_resp = manager.batch_create_memory_records(
        memoryId=memory_id,
        records=[
            {
                "requestIdentifier": "note-lang",
                "namespaces": [NAMESPACE],
                # timestamp is a datetime-typed field; pass a real datetime, not a string.
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex prefers Python over Java."},
            },
            {
                "requestIdentifier": "note-city",
                "namespaces": [NAMESPACE],
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex is based in Berlin."},
            },
            {
                "requestIdentifier": "note-allergy",
                "namespaces": [NAMESPACE],
                "timestamp": datetime.now(timezone.utc),
                "content": {"text": "Alex is allergic to peanuts."},
            },
        ],
    )
    successes = create_resp.get("successfulRecords", [])
    print(f"[sdk] Created {len(successes)} ({len(create_resp.get('failedRecords', []))} failed)")
    record_ids = {r["requestIdentifier"]: r["memoryRecordId"] for r in successes}

    # The just-created records are eventually consistent — retry the dependent
    # update/delete until they propagate (BatchUpdateMemoryRecords requires
    # memoryRecordId AND timestamp).
    update_resp = _retry_until_propagated(
        lambda: manager.batch_update_memory_records(
            memoryId=memory_id,
            records=[
                {
                    "memoryRecordId": record_ids["note-lang"],
                    "timestamp": datetime.now(timezone.utc),
                    "content": {"text": "Alex prefers Python and writes Rust for hot paths."},
                }
            ],
        )
    )
    print(f"[sdk] Updated {len(update_resp.get('successfulRecords', []))}")

    delete_resp = _retry_until_propagated(
        lambda: manager.batch_delete_memory_records(
            memoryId=memory_id,
            records=[{"memoryRecordId": record_ids["note-allergy"]}],
        )
    )
    print(f"[sdk] Deleted {len(delete_resp.get('successfulRecords', []))}")

    # list_long_term_memory_records is a first-class MemorySessionManager method
    # (returns a list of MemoryRecord directly, not a {"memoryRecordSummaries": ...} dict).
    # Same eventual-consistency poll as the boto3 path above.
    print(f"\n[sdk] Polling up to {PROPAGATION_WAIT_SECONDS}s for records to become listable...")
    remaining = _list_until_visible(lambda: manager.list_long_term_memory_records(namespace=NAMESPACE))
    print(f"\n[sdk] Remaining ({len(remaining)}):")
    for r in remaining:
        print(f"  - {r['content']['text']}")
    if len(remaining) != EXPECTED_REMAINING:
        print(f"[sdk] Expected {EXPECTED_REMAINING} records after {PROPAGATION_WAIT_SECONDS}s — still propagating.")

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
