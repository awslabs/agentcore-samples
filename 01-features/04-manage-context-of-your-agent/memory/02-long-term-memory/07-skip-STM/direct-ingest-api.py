"""IngestData — feed long-term memory without persisting a short-term event.

`CreateEvent` does two jobs: it stores the turn as short-term conversation
history AND queues it for extraction. `IngestData` does only the second. The
content is buffered and extracted on each strategy's normal trigger, but no
event is written, so `ListEvents` stays empty and nothing counts against your
short-term history.

What you learn:
    - IngestData with a conversational payload (the CreateEvent equivalent)
    - IngestData with a `json` payload — telemetry and app state, no chat needed
    - Namespace substitution per call via `extractionConfig.namespaceVariables`
    - `metadata` to enrich the records extraction produces
    - The sessionId contract: echoed back, or server-generated when you omit it

The use case: a car-dealership assistant
    Most of what it should remember never gets said out loud — cars viewed,
    filters applied, a financing pre-approval. Those are app events, not turns,
    and writing them as fake conversation pollutes the transcript the model
    reads back. IngestData sends them straight to extraction instead.

Run:
    python direct-ingest-api.py [--cleanup]

boto3 only — the AgentCore SDK does not wrap IngestData yet.

Prerequisites:
    pip install --upgrade boto3   # IngestData must be in the bundled model
    export AWS_REGION=us-west-2

If your boto3 predates the API, botocore rejects the call locally before it is
ever sent; the script detects that up front and tells you to upgrade rather
than leaving you with an opaque ParamValidationError.
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.getenv("AWS_REGION", "us-west-2")
EXTRACTION_WAIT_SECONDS = 180  # extraction is async and trigger-driven; poll, don't assume

ACTOR_ID = "customer-123"
SESSION_ID = f"shopping-{int(time.time())}"

# {dealership} is a custom variable, so it has to be declared on the memory
# (namespaceKeys) and resolved on every call that should land under it.
FACTS_TEMPLATE = "/dealerships/{dealership}/customers/{actorId}/facts/"
PREFERENCES_TEMPLATE = "/dealerships/{dealership}/customers/{actorId}/preferences/"
NAMESPACE_PREFIX = f"/dealerships/westside/customers/{ACTOR_ID}/"

control = boto3.client("bedrock-agentcore-control", region_name=REGION)
data = boto3.client("bedrock-agentcore", region_name=REGION)


def wait_active(memory_id: str) -> None:
    deadline = time.time() + 300
    while control.get_memory(memoryId=memory_id)["memory"]["status"] != "ACTIVE":
        if time.time() > deadline:
            raise TimeoutError(f"{memory_id} never became ACTIVE")
        time.sleep(5)


def ingest(memory_id: str, payload: list, *, session_id=None, **kwargs) -> str:
    """One IngestData call. Returns the sessionId the content landed in.

    contentTimestamp is the *business* time of the content — when the customer
    viewed the car, not when you got around to shipping it. Backfilling a day of
    telemetry means a day of real timestamps, not `now` repeated.

    clientToken makes the call idempotent: a retry with the same token is
    absorbed instead of double-ingesting the same payload.
    """
    request = {
        "memoryId": memory_id,
        "actorId": ACTOR_ID,
        "contentTimestamp": datetime.now(timezone.utc),
        "clientToken": str(uuid.uuid4()),
        "source": {"inline": {"payload": payload}},
        **kwargs,
    }
    if session_id is not None:  # omit entirely to have the service generate one
        request["sessionId"] = session_id
    return data.ingest_data(**request)["sessionId"]


def main(cleanup: bool = False) -> None:
    if "IngestData" not in data.meta.service_model.operation_names:
        sys.exit(f"boto3 {boto3.__version__} does not model IngestData yet — pip install --upgrade boto3")

    # === 1. A memory whose strategies decide what gets extracted ==========
    # IngestData feeds the same strategies as CreateEvent; nothing about the
    # memory resource is ingest-specific.
    memory_id = control.create_memory(
        name=f"DirectIngest_{int(time.time())}",
        description="IngestData tutorial — dealership assistant",
        eventExpiryDuration=30,
        memoryStrategies=[
            {"semanticMemoryStrategy": {"name": "Facts", "namespaceTemplates": [FACTS_TEMPLATE]}},
            {"userPreferenceMemoryStrategy": {"name": "Prefs", "namespaceTemplates": [PREFERENCES_TEMPLATE]}},
        ],
        namespaceKeys=[{"key": "dealership", "validation": {"allowedValues": ["westside", "eastside"]}}],
    )["memory"]["id"]
    print(f"Created memory {memory_id}")
    wait_active(memory_id)

    dealership = {"namespaceVariables": {"dealership": "westside"}}

    # === 2. Conversational content — what CreateEvent would have taken ====
    session_id = ingest(
        memory_id,
        [
            {
                "conversational": {
                    "role": "USER",
                    "content": {"text": "Automatic sedan please. I really liked the Corolla."},
                }
            },
            {
                "conversational": {
                    "role": "ASSISTANT",
                    "content": {"text": "Good choice — you're pre-approved at 5.9% APR. Want me to hold it?"},
                }
            },
        ],
        session_id=SESSION_ID,
        extractionConfig=dealership,
    )
    print(f"Ingested conversational turns into session {session_id}")

    # === 3. JSON content — the reason to reach for this API ===============
    # No role, no text: `json.content` is an arbitrary document. Extraction reads
    # the structure, so use field names that mean something ("view_duration_sec",
    # not "d2"). `metadata` is carried onto the resulting records, which is how
    # you filter later without parsing the text back out.
    ingest(
        memory_id,
        [
            {
                "json": {
                    "content": {
                        "event": "search_filter_applied",
                        "filters": {
                            "body_style": "sedan",
                            "min_year": 2021,
                            "max_price": 23000,
                            "transmission": "automatic",
                            "make_preference": ["Honda", "Toyota", "Mazda"],
                        },
                    }
                }
            },
            {
                "json": {
                    "content": {
                        "event": "financing_pre_approved",
                        "term_months": 48,
                        "apr": 5.9,
                        "max_amount": 25000,
                    }
                }
            },
        ],
        session_id=SESSION_ID,
        extractionConfig=dealership,
        metadata={"channel": "web", "source_system": "crm"},
    )
    print("Ingested 2 JSON app events (no conversation involved)")

    # === 4. Mixed payload — both content types in one call ================
    ingest(
        memory_id,
        [
            {"conversational": {"role": "USER", "content": {"text": "Can I drive the silver one this weekend?"}}},
            {
                "json": {
                    "content": {
                        "event": "test_drive_scheduled",
                        "car_id": "VH-2093",
                        "location": "CDMX-Polanco",
                        "date": "2026-08-20",
                    }
                }
            },
        ],
        session_id=SESSION_ID,
        extractionConfig=dealership,
    )
    print("Ingested a mixed conversational + JSON payload")

    # Omitting sessionId is legal — the service mints a UUID and returns it. Keep
    # it if you want the content grouped; a fresh session per call fragments the
    # context strategies get to work with.
    generated = ingest(
        memory_id,
        [{"json": {"content": {"event": "car_viewed", "car_id": "VH-1044", "view_duration_sec": 112}}}],
        extractionConfig=dealership,
    )
    print(f"Ingested with no sessionId — service generated {generated}")

    # === 5. The point: records without events ============================
    events = data.list_events(memoryId=memory_id, actorId=ACTOR_ID, sessionId=SESSION_ID)["events"]
    print(f"\nShort-term events in {SESSION_ID}: {len(events)}  <- IngestData persists none")

    print(f"Polling up to {EXTRACTION_WAIT_SECONDS}s for extraction...")
    records, deadline = [], time.time() + EXTRACTION_WAIT_SECONDS
    while not records and time.time() < deadline:
        records = data.list_memory_records(memoryId=memory_id, namespacePath=NAMESPACE_PREFIX)["memoryRecordSummaries"]
        if not records:
            time.sleep(10)

    print(f"\nLong-term records under {NAMESPACE_PREFIX} ({len(records)}):")
    for record in records:
        print(f"  - [{','.join(record.get('namespaces', []))}] {record['content']['text'][:70]}")

    if cleanup:
        control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        print(f"\nDeleted memory {memory_id}")
    else:
        print(f"\nKeeping memory {memory_id} (pass --cleanup to delete)")


if __name__ == "__main__":
    main(cleanup="--cleanup" in sys.argv[1:])
