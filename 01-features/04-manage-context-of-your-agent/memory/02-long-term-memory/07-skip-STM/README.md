# Writing long-term memory without short-term memory

`CreateEvent` always writes a short-term event. These two APIs don't — they populate long-term memory while leaving conversation history untouched, entering the pipeline at opposite ends:

| Script | API | Where it enters |
|---|---|---|
| [`direct-ingest-api.py`](./direct-ingest-api.py) | `IngestData` | **Before** extraction — content is buffered, strategies extract it on their normal trigger |
| [`batch-create-update-delete.py`](./batch-create-update-delete.py) | `BatchCreate/Update/DeleteMemoryRecords` | **After** extraction — you write finished records yourself |

Pick by who does the extracting. If you want AgentCore's strategies to read your content, use `IngestData`. If you already have the records, use the batch APIs.

## `IngestData`

`CreateEvent` does two jobs: it stores the turn as short-term conversation history *and* queues it for extraction. `IngestData` does only the second. No event is persisted, so `ListEvents` stays empty.

```python
data.ingest_data(
    memoryId=memory_id,
    actorId="customer-123",
    sessionId="shopping-42",          # optional — omit and the service returns a generated one
    contentTimestamp=datetime.now(timezone.utc),
    clientToken=str(uuid.uuid4()),    # idempotency
    source={"inline": {"payload": [
        {"conversational": {"role": "USER", "content": {"text": "Automatic sedan please."}}},
        {"json": {"content": {"event": "car_viewed", "car_id": "VH-1044", "view_duration_sec": 112}}},
    ]}},
    extractionConfig={"namespaceVariables": {"dealership": "westside"}},
    metadata={"channel": "web"},
)
```

Three things shown together here and are easy to conflate:

- **`IngestData`** — the extraction-only entry point (this folder).
- **`json` payloads** — non-conversational content, that could be any arbitrary information under `json.content`: telemetry, app state, a CRM row, metadata from the UI.  `IngestData` (and `CreateEvent` API) covers all three types (`conversational`, `json`, `blob`). the 100 KB limit, and the field-naming rules extraction cares about.
- **Namespace substitution** — `extractionConfig.namespaceVariables` resolves your own template variables per call. Declared on the memory via `namespaceKeys`; see [`../04-namespaces/`](../04-namespaces/).

### What you learn

- Conversational, `json`, and mixed payloads in a single call
- Resolving custom namespace variables per ingest
- `metadata` carried onto the records extraction produces
- The `sessionId` contract — echoed back, or server-generated when omitted
- Proving the difference: `ListEvents` empty, `ListMemoryRecords` populated

### When to use

- **Signal that was never said out loud** — cars viewed, filters applied, a financing approval. Real preferences that only exist as app events.
- **Backfills** — load history into extraction without inventing fake conversations. Pass the real `contentTimestamp` per item, not `now`.
- **You manage transcripts yourself** — you already have conversation history in your own store and only want AgentCore's extraction.

### Best practices

- **`contentTimestamp` is business time**, not wall-clock time of the call. It drives recency in extraction and consolidation.
- **Always send `clientToken`.** The API is idempotent on it; a retry after a timeout is absorbed rather than double-ingested.
- **Reuse one `sessionId`** for related content. A fresh session per call fragments the context strategies get to work with.
- **202, not 200.** Extraction is trigger-driven (message count, tokens, or time), so records appear later than the call returns. Poll `ListMemoryRecords`.
- **Don't drop `CreateEvent`** where you need conversation history — `IngestData` writes no event, so there is nothing to read back into a prompt.

## Batch record CRUD

Direct CRUD on records, bypassing the strategy pipeline entirely:

| API | Purpose |
|---|---|
| `BatchCreateMemoryRecords` | Insert pre-extracted records (up to 100 per call) |
| `BatchUpdateMemoryRecords` | Overwrite content on existing records |
| `BatchDeleteMemoryRecords` | Remove records by id |

Each call reports per-record success and failure independently — partial success is the norm, so always inspect `successfulRecords` and `failedRecords`.

### What you learn

- Inserting records you extracted yourself, e.g. from a self-managed strategy worker
- Updating record content in place by `memoryRecordId`
- Deleting records by id without touching the underlying events
- Reading per-record outcomes out of `successfulRecords` / `failedRecords`

### When to use

- **Self-managed strategy** — your worker extracted records out-of-band and writes them back with `BatchCreateMemoryRecords`.
- **Back-fills and migrations** — load records from another store into a new memory resource.
- **Admin tooling** — surgical edits or deletions for compliance (right-to-be-forgotten, redaction).

### Best practices

- **Always pass `requestIdentifier`** on creates — it maps responses back to your own data and makes the call idempotent.
- **Inspect `failedRecords`** on every call. The API returns 200 even when individual records fail.
- **Cap at 100 records per call** — split larger workloads into chunks and parallelize.
- **Created records are eventually consistent.** A record reported `SUCCEEDED` is not immediately updatable or listable; `BatchUpdate`/`Delete`/`List` can raise `ResourceNotFoundException` for seconds to a minute. Retry the dependent operation (the script shows the pattern) instead of assuming availability.
- **Updates are full overwrites** of `content.text`. There is no patch semantics.
- **Don't bypass extraction unintentionally.** Batch CRUD is for records you have already extracted. If you want AgentCore to do the extracting, use `IngestData` (above) or `CreateEvent` with a strategy attached.

## Run

```bash
pip install --upgrade boto3 bedrock-agentcore
export AWS_REGION=us-west-2

python direct-ingest-api.py                      # IngestData — boto3 only, the SDK doesn't wrap it yet
python batch-create-update-delete.py boto3       # batch CRUD, direct service calls
python batch-create-update-delete.py sdk         # batch CRUD via MemorySessionManager
```

Both keep the memory resource by default and print its `memoryId`; add `--cleanup` to delete it at the end.

`IngestData` needs a boto3 recent enough to model it — botocore validates against its bundled model and rejects the call locally otherwise. `direct-ingest-api.py` checks for the operation up front and tells you to upgrade.

## AWS CLI walkthrough

```bash
# 1. Create memory. Strategies matter for ingest-data; batch CRUD needs none.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "DirectIngestCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-strategies '[{"semanticMemoryStrategy":{"name":"Facts",
     "namespaceTemplates":["/customers/{actorId}/facts/"]}}]' \
  --query 'memory.id' --output text)

# Wait until ACTIVE. Writes are rejected while the memory is still CREATING, and
# creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. IngestData — conversational + JSON in one payload, no event persisted
aws bedrock-agentcore ingest-data \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-123 --session-id shopping-42 \
  --content-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --client-token "$(uuidgen)" \
  --source '{"inline":{"payload":[
    {"conversational":{"role":"USER","content":{"text":"Automatic sedan please."}}},
    {"json":{"content":{"event":"car_viewed","car_id":"VH-1044","view_duration_sec":112}}}
  ]}}'

# 3. Confirm: no short-term events, but records show up after extraction
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-123 --session-id shopping-42
sleep 120
aws bedrock-agentcore list-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace-path "/customers/customer-123/"

# 4. BatchCreate — records you extracted yourself
aws bedrock-agentcore batch-create-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --records '[
    {"requestIdentifier":"note-lang","namespaces":["/users/user-alex/notes/"],
     "timestamp":"'"$(date +%s)"'",
     "content":{"text":"Alex prefers Python over Java."}},
    {"requestIdentifier":"note-city","namespaces":["/users/user-alex/notes/"],
     "timestamp":"'"$(date +%s)"'",
     "content":{"text":"Alex is based in Berlin."}}
  ]'
# Capture memoryRecordId values from the response.

# 5. BatchUpdate / BatchDelete (retry these — created records propagate first)
aws bedrock-agentcore batch-update-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --records '[{"memoryRecordId":"<id>","timestamp":"'"$(date +%s)"'",
               "content":{"text":"updated text"}}]'
aws bedrock-agentcore batch-delete-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --records '[{"memoryRecordId":"<id>"}]'

# 6. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
