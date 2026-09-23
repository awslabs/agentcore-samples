# Error handling

AgentCore Memory has two API surfaces, and they raise **different exceptions**. Getting this right matters because the retry decision depends on the exact error.

- **Data plane** — `bedrock-agentcore` (boto3) / `MemoryClient` (SDK): `CreateEvent`, `RetrieveMemoryRecords`, `ListEvents`, `BatchCreate/Update/DeleteMemoryRecords`, …
- **Control plane** — `bedrock-agentcore-control`: `CreateMemory`, `UpdateMemory`, `DeleteMemory`, …

All errors arrive in boto3 as `botocore.exceptions.ClientError`; the modeled name is in `e.response["Error"]["Code"]` and the HTTP status in `e.response["ResponseMetadata"]["HTTPStatusCode"]`.

## The exceptions, by surface

### Data-plane errors

Sourced from the API reference for [`CreateEvent`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_CreateEvent.html), [`RetrieveMemoryRecords`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_RetrieveMemoryRecords.html), and [`BatchCreateMemoryRecords`](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_BatchCreateMemoryRecords.html).

| Exception | HTTP | Retry-able? | What it means / what to do |
|---|---|---|---|
| `ThrottledException` | 429 | **Yes** | Request rate exceeded. Back off exponentially with jitter. If sustained, you've hit an account quota — request an increase. |
| `RetryableConflictException` | 409 | **Yes** | Temporary conflict (e.g. concurrent writes to the same session). The API reference explicitly recommends exponential-backoff retry. **Only `CreateEvent` raises this** — not retrieve/batch. |
| `ServiceException` | 500 | **Yes** | Internal service error. Transient; retry with backoff. If it persists, open a support case. |
| `ServiceQuotaExceededException` | 402 | **No** (not by blind retry) | A quota would be exceeded. Retrying the same request won't help — reduce the request or request a quota increase. |
| `ValidationException` | 400 | **No** | Input violates a service constraint (bad namespace, oversized metadata, malformed payload). Fix the input; never retry as-is. |
| `InvalidInputException` | 400 | **No** | Input failed AgentCore-side validation. Same as above — fix, don't retry. |
| `ResourceNotFoundException` | 404 | **No** | Memory, strategy, or namespace doesn't exist (or was deleted). Retrying won't conjure it. Check the id / that the resource is `ACTIVE`. |
| `AccessDeniedException` | 403 | **No** | IAM/KMS denied the call. Fix the policy or key state. A retry storms your own logs. |

### Control-plane errors

Sourced from the [`CreateMemory`](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateMemory.html) API reference. Same retry-ability logic; two differences from the data plane:

| Exception | HTTP | Retry-able? | Notes |
|---|---|---|---|
| `ThrottledException` | 429 | **Yes** | Same as data plane. |
| `ConflictException` | 409 | **No** | A conflicting operation (e.g. creating a memory whose name already exists, or mutating a resource mid-update). Not the same as the data plane's `RetryableConflictException` — resolve the conflict, don't blindly retry. |
| `ServiceException` | 500 | **Yes** | Transient. |
| `ServiceQuotaExceededException` | 402 | **No** | Quota; request an increase. |
| `ValidationException` | 400 | **No** | Bad input — e.g. `eventExpiryDuration` outside 3–365, or a name that doesn't match `[a-zA-Z][a-zA-Z0-9_]{0,47}`. |
| `ResourceNotFoundException` | 404 | **No** | Resource doesn't exist. |
| `AccessDeniedException` | 403 | **No** | Permissions. |

> **Naming gotcha.** The modeled throttling exception is `ThrottledException`. That's distinct from the string `ThrottlingException` you may see inside an **extraction job's** `failureReason` — that one is the *Bedrock model* throttling the async extraction, not the Memory API throttling your call. See [`../02-long-term-memory/08-redrive/`](../02-long-term-memory/08-redrive/) for redriving those. Match on the code you actually observe rather than hard-coding one spelling.

## The one rule

> **Retry transient failures. Fix deterministic ones.**

Throttling, retryable conflicts, and 500s are transient — they may succeed on the next attempt. Validation, not-found, access-denied, and quota-exceeded are deterministic — the *same request* will fail the *same way* every time, so retrying just burns latency, tokens, and log volume. The `_is_retryable` helper in [`production-patterns.py`](./production-patterns.py) encodes exactly this split.

## Exponential backoff with jitter

Never retry in a tight loop, and never retry on a fixed delay — fixed delays cause **retry storms** where every throttled client retries in lockstep and re-throttles the service. Use exponential backoff with **full jitter**:

```
delay = random_uniform(0, min(cap, base * 2 ** attempt))
```

```python
import random
import time
from botocore.exceptions import ClientError

# Codes worth retrying. Everything else is deterministic — fix it, don't retry.
RETRYABLE = {
    "ThrottledException",
    "RetryableConflictException",
    "ServiceException",
}

def call_with_backoff(fn, *args, max_attempts=5, base=0.5, cap=20.0, **kwargs):
    """Call `fn`, retrying only transient errors with full-jitter backoff."""
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in RETRYABLE or attempt == max_attempts - 1:
                raise  # deterministic, or out of attempts — let it surface
            delay = random.uniform(0, min(cap, base * (2 ** attempt)))
            time.sleep(delay)
```

### You're not starting from zero: botocore already retries

boto3's default (`standard`) retry mode **already** retries throttling and transient errors with exponential backoff — up to 3 attempts. You can raise that, or switch to `adaptive` mode (client-side rate limiting that backs off proactively), via the client config:

```python
import boto3
from botocore.config import Config

data = boto3.client(
    "bedrock-agentcore",
    config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
)
```

> The `bedrock_agentcore` `MemoryClient` builds its own boto3 clients internally. As of the SDK version inspected for this guide (1.12.0), it sets a `user_agent_extra` on the client config but does **not** raise `max_attempts` or set `adaptive` mode — so you get botocore's default `standard`/3-attempts behaviour. Treat the application-level backoff above as a deliberate layer on top of (not instead of) botocore's, especially for batch and bulk paths where you want a higher attempt ceiling.

The application-level `call_with_backoff` is still worth keeping because it lets you:
- decide retry-ability per *modeled exception name* (botocore retries by category, not by your business rules),
- bound attempts differently for interactive vs. batch paths,
- emit your own metrics/logs on each retry.

## Graceful degradation

**Memory is an enhancement, not a hard dependency on the response path.** If retrieval is throttled or the service is briefly down, the agent should still answer — just without the remembered context — rather than fail the user's turn.

```python
def recall(client, memory_id, **kwargs):
    """Retrieve memories, but never let a memory failure break the turn."""
    try:
        return call_with_backoff(client.retrieve_memories, memory_id=memory_id, **kwargs)
    except ClientError as e:
        # Degrade: log, emit a metric, and continue with empty context.
        logger.warning("Memory recall failed (%s); continuing without it",
                       e.response["Error"]["Code"])
        return []
```

Two asymmetries to respect:

- **Reads** (`RetrieveMemoryRecords`, `ListEvents`) degrade cleanly to "no context" — the user gets a slightly less personalized answer. The SDK's own `retrieve_memories` already swallows `ResourceNotFoundException`/`ValidationException`/`ServiceException` and returns `[]` for exactly this reason.
- **Writes** (`CreateEvent`) are different: dropping a write silently loses conversation history. Prefer to **buffer-and-retry** writes (in-memory queue, or persist to a durable buffer) rather than discard them. Only drop a write as a last resort, and emit a metric when you do.

## Partial failure on batch calls

`BatchCreate/Update/DeleteMemoryRecords` return **HTTP 200/201 even when individual records fail**. The per-record outcome is in `successfulRecords` / `failedRecords` — the top-level call succeeding tells you nothing about the records. Always inspect both:

```python
resp = call_with_backoff(data.batch_create_memory_records, memoryId=mid, records=batch)
failed = resp.get("failedRecords", [])
if failed:
    for r in failed:
        logger.error("record %s failed: %s %s",
                     r.get("requestIdentifier"), r.get("errorCode"), r.get("errorMessage"))
    # Decide per errorCode whether the failed subset is worth re-submitting.
```

Cap each batch at **100 records** (the documented maximum for `records`); split larger workloads into chunks. See [`../02-long-term-memory/07-skip-STM/`](../02-long-term-memory/07-skip-STM/).

## try / finally cleanup

Anything you create in a request path or a test must be released even when the body raises. The classic leak is a memory resource (and its ongoing storage cost) orphaned by an exception between create and delete.

```python
memory_id = None
try:
    memory_id = control.create_memory(name=name, eventExpiryDuration=30)["memory"]["id"]
    # ... use the memory ...
finally:
    if memory_id:
        try:
            control.delete_memory(memoryId=memory_id, clientToken=str(uuid.uuid4()))
        except ClientError as e:
            # Don't let cleanup failure mask the original error; log and move on.
            logger.error("cleanup of %s failed: %s", memory_id, e.response["Error"]["Code"])
```

Key points:
- The cleanup itself is wrapped — a failing `delete_memory` must not replace the real exception from the `try` block.
- `delete_memory` takes a `clientToken` for idempotency; pass a fresh UUID.
- For long-lived production resources you do **not** delete per request — see the teardown guidance in [`03-production-checklist.md`](./03-production-checklist.md#7-resource-cleanup-on-teardown).

See [`production-patterns.py`](./production-patterns.py) for all of these composed into one reference module.
</content>
