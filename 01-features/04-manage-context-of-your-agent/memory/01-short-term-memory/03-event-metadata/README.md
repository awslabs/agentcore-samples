# Event metadata and filtering

Attach key-value metadata to each event so you can filter `ListEvents` later without scanning the whole session. Examples: tag events by topic, channel, priority, or downstream-pipeline routing key.

## What you learn

- Adding metadata on `CreateEvent` (max 15 keys per event, 128-char keys, 256-char string values)
- Filtering `ListEvents` with `EQUALS_TO`, `EXISTS`, `NOT_EXISTS`
- Composing up to 5 filter expressions per request (logical AND across them)

## Metadata vs. a `json` payload

Both attach structure to an event, and they are easy to conflate. They do opposite jobs:

| | Event metadata | `json` payload item |
|---|---|---|
| Purpose | Filtering and routing | Extraction input |
| Filterable by `ListEvents` | Yes (`EQUALS_TO`, `EXISTS`, `NOT_EXISTS`) | No |
| Extracted into long-term memory | Never | Yes |
| Size | 15 keys, 128-char keys, 256-char string values | Up to 100 KB per item |
| Encrypted with your CMK | No | Yes |

Tag an event `{"channel": "web"}` so you can find it later; put the behaviour itself — the filters applied, the vehicle viewed — in a `json` payload so a strategy can learn from it. See [`../02-payload-types/`](../02-payload-types/).

## Run

```bash
python event-metadata-filtering.py boto3   # default — direct service calls
python event-metadata-filtering.py sdk     # documents the SDK gap (no metadata=)
```

## Best practices

- **Keep metadata small and bounded.** Use stable, low-cardinality keys (`topic`, `priority`, `channel`) — not free-form text.
- **Do not store sensitive data in metadata.** Event metadata is **not** encrypted with your customer-managed KMS key, even when the memory resource is. Keep PII/PHI in `payload`.
- Filter values are exact-match (`EQUALS_TO`); for free-text search you want long-term memory, not metadata filters.
- Plan your metadata schema up front — `indexedKeys` on `CreateMemory` cannot be removed once declared.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "EventMetaCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Append an event with metadata. Metadata values are typed (stringValue).
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"I had a fever."}}}]' \
  --metadata '{"topic":{"stringValue":"health"},"priority":{"stringValue":"high"}}'

# 3. ListEvents filtered to topic=health
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli --include-payloads \
  --filter '{
    "eventMetadata": [{
      "left":  {"metadataKey": "topic"},
      "operator": "EQUALS_TO",
      "right": {"metadataValue": {"stringValue": "health"}}
    }]
  }'

# 4. ListEvents filtered to events that have a priority key set
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli --include-payloads \
  --filter '{"eventMetadata":[{"left":{"metadataKey":"priority"},"operator":"EXISTS"}]}'

# 5. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
