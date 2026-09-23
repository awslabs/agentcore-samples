# Event payload types

An event's `payload` is a **list of payload items**, and not every item is a conversation turn. The same `CreateEvent` call that stores speech also stores structured application data — clicks, filters, approvals, telemetry — and extraction reads it the same way it reads speech.

| Type | Content | Extracted into long-term memory? |
|---|---|---|
| `conversational` | A turn: `{"role": "USER", "content": {"text": "..."}}` | Yes |
| `json` | Any structured document under `json.content`, up to **100 KB** per item | Yes |
| `blob` | Arbitrary data — a document, an encoded file, agent-specific state | **No** — short-term memory only |

That last column is the whole point of this folder. A `json` payload is not a storage bucket; it is extraction input. A `blob` payload is the opposite: it round-trips through `ListEvents`/`GetEvent` unchanged, and no strategy ever looks at it.

```python
data.create_event(
    memoryId=memory_id,
    actorId="customer-456",
    sessionId="shopping-42",
    eventTimestamp=datetime.now(timezone.utc),
    payload=[
        {"conversational": {"role": "USER", "content": {"text": "Automatic sedan please."}}},
        {"json": {"content": {"event": "car_viewed", "car_id": "VH-1044", "view_duration_sec": 112}}},
    ],
)
```

## What you learn

- Writing `conversational`, `json`, and `blob` payload items with `CreateEvent`
- Mixed payloads — several items of different types in one event
- Which types reach long-term memory, proved by retrieval rather than asserted
- The 100 KB per-`json`-item limit
- Two SDK gaps: no JSON message type, and `get_last_k_turns` drops non-conversational items

## Run

```bash
python payload-types.py boto3   # default — direct service calls, all three types
python payload-types.py sdk     # AgentCore SDK; documents the JSON gap
```

Both keep the memory resource and print its `memoryId`; add `--cleanup` to delete it.

The script runs a car-dealership session: four `json`-only events (`car_viewed`, `search_filter_applied`, `financing_pre_approved`, `test_drive_scheduled`), one mixed conversational + `json` event, and one `blob` event. It then retrieves the extracted facts and preferences — all of which trace back to the JSON payloads, none to the blob.

This tutorial attaches semantic and user-preference strategies even though it sits under short-term memory. The difference between the three types *is* what extraction does with them, and that can't be shown without a strategy.

## SDK support

`MemorySessionManager.add_turns` takes `ConversationalMessage` and `BlobMessage`. There is **no JSON message type** as of `bedrock-agentcore` 1.22, so `json` payloads go through boto3 `create_event`:

```python
from bedrock_agentcore.memory.constants import BlobMessage, ConversationalMessage, MessageRole

# One add_turns call = one event, so mixing message types gives you a mixed payload.
session.add_turns(messages=[
    ConversationalMessage("Automatic sedan please.", MessageRole.USER),
    BlobMessage({"document": "appraisal.pdf", "bytes": "..."}),
])
# json: no SDK type — use data.create_event(payload=[{"json": {"content": {...}}}])
```

## Best practices

- **Field names are prompt input.** Extraction reasons over your JSON structure, so `view_duration_sec` earns its keep where `d2` tells the model nothing. Name fields as you would for a human reader.
- **Send the event that happened, not a sentence about it.** Don't synthesise `"The user viewed a Honda Civic"` as a fake conversational turn — write the `json` item. You keep the real structure, and the transcript stays an honest record of what was said.
- **Mix types in one event when they describe one moment.** Speech plus the behaviour that accompanied it gives extraction corroborating signal; the doc's own output shows preferences justified by both at once ("explicitly stated 'Automatic sedan please' *and* applied search filters").
- **Check the 100 KB limit before you send.** It is per `json` item, not per event — split a large document across items (up to 100 per payload), or store it as a `blob` if nothing needs extracting from it.
- **Use `blob` only for data you never want extracted.** Binary content, large documents, agent-internal state. If you want it to influence retrieval, it needs to be `json`.
- **Don't rehydrate prompts with `get_last_k_turns` if JSON payloads carry real context.** It returns conversational items only, so structured context silently never reaches the model. Use `list_events(include_payload=True)` and handle each item type yourself.
- **`json` is not a metadata substitute.** JSON payloads are extracted but not filterable; event metadata is filterable but never extracted. See [`../03-event-metadata/`](../03-event-metadata/).

## Where to go next

- What the strategies do with JSON payloads: [`../../02-long-term-memory/01-built-in-strategies/`](../../02-long-term-memory/01-built-in-strategies/)
- Same payload types, extraction without a short-term event: [`../../02-long-term-memory/07-skip-STM/`](../../02-long-term-memory/07-skip-STM/)
- Filterable key-value tags on an event: [`../03-event-metadata/`](../03-event-metadata/)

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory with the two strategies that will read the JSON payloads
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "PayloadTypesCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-strategies '[
    {"semanticMemoryStrategy":{"name":"ShopperFacts",
      "namespaces":["/customers/{actorId}/facts/"]}},
    {"userPreferenceMemoryStrategy":{"name":"ShopperPreferences",
      "namespaces":["/customers/{actorId}/preferences/"]}}
  ]' \
  --query 'memory.id' --output text)

# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. A json-only event — behaviour the shopper never said out loud
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-456 --session-id shopping-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"json":{"content":{"event":"car_viewed","car_id":"VH-1044",
    "make":"Honda","model":"Civic","year":2022,"transmission":"automatic",
    "view_duration_sec":112,"price":21000}}}]'

# 3. A mixed event — speech and behaviour in one payload
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-456 --session-id shopping-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[
    {"conversational":{"role":"USER",
      "content":{"text":"Automatic sedan please. I really liked the Corolla."}}},
    {"json":{"content":{"event":"car_viewed","car_id":"VH-3310",
      "make":"Toyota","model":"Corolla","year":2023,"view_duration_sec":185}}},
    {"conversational":{"role":"ASSISTANT",
      "content":{"text":"Good choice. You are pre-approved at 5.9% APR."}}}
  ]'

# 4. A blob event — stored, never extracted
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-456 --session-id shopping-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"blob":{"document":"trade-in-appraisal.pdf","encoding":"base64",
    "bytes":"JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PAo="}}]'

# 5. All three types are in short-term memory
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id customer-456 --session-id shopping-cli --include-payloads

# 6. Wait for extraction, then confirm only conversational + json produced records
sleep 120
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/customers/customer-456/preferences/" \
  --search-criteria '{"searchQuery":"body style and transmission preference","topK":5}'

# 7. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
