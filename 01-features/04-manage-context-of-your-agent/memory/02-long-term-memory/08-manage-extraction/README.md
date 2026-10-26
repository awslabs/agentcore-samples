# Managing extraction

This folder covers the two controls you have over the extraction lifecycle:

- **Skip extraction** — prevent specific events from being extracted into long-term memory (`extractionMode="SKIP"` on `CreateEvent`)
- **Redrive failed extraction** — retry jobs that failed due to transient errors (`StartMemoryExtractionJob`)

## Skip extraction

Events marked with `extractionMode="SKIP"` are stored in short-term memory normally but are never sent through the extraction pipeline. Common use cases:

| Use case | Why skip |
|---|---|
| Bulk import / backfill | Historical events already have records elsewhere; extraction would duplicate or conflict |
| System / tool messages | Internal plumbing (tool calls, debug info) that shouldn't become long-term facts |
| Sensitive turns | Content that should be available in-session but not persisted as records |
| Cross-region replication | Events copied from another region shouldn't be re-extracted |

### Run

```bash
pip install -r ../requirements.txt   # extractionMode needs boto3>=1.43.35
python skip-extraction.py boto3   # direct service calls
python skip-extraction.py sdk     # AgentCore MemoryClient helpers
```

## Redrive failed extraction

Extraction runs asynchronously after `CreateEvent`. When it fails — model throttle, transient error, malformed payload — AgentCore records the attempt as an extraction job with `status=FAILED` and a `failureReason`. You can list those jobs and redrive them.

### When to redrive vs. investigate first

| Symptom | Action |
|---|---|
| Model throttle / `ThrottlingException` | Safe to redrive after a delay |
| `AccessDeniedException` on the strategy's model | Fix IAM / model access first, then redrive |
| Validation error on payload structure | Don't redrive — the payload is bad |
| Unknown / generic service error | Open a support case before redriving in bulk |

### Run

```bash
# Quote the placeholder — unquoted, bash reads < as a redirect and the paste fails.
export MEMORY_ID="<a memoryId that has failed extraction jobs>"
python redrive-failed-extractions.py boto3
python redrive-failed-extractions.py sdk
```

## Best practices

- **Default to normal extraction.** Only use `SKIP` when you have a clear reason — skipped events leave no long-term trace.
- **Combine skip with metadata.** Tag skipped events with metadata (`"skipped": "true"`) so you can identify them later in `ListEvents`.
- **Read `failureReason` before redriving.** A blind retry on a deterministic failure just burns tokens.
- **Throttle redrives.** Space out bulk redrives — the underlying cause may be capacity-related.
- **Use streaming to confirm.** Subscribe to `MemoryRecordCreated` events to verify extraction (or redrive) produced records.

## AWS CLI walkthrough

### Skip extraction

```bash
# Send an event that will NOT be extracted into long-term memory
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-import \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --extraction-mode SKIP \
  --payload '[{"conversational":{"role":"USER","content":{"text":"historical import — do not extract"}}}]'
```

### Redrive failed extraction

```bash
# 1. List failed extraction jobs
aws bedrock-agentcore list-memory-extraction-jobs \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --filter '{"status":"FAILED"}'

# 2. Redrive a single job (after fixing the underlying issue)
aws bedrock-agentcore start-memory-extraction-job \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --extraction-job '{"jobId":"<jobId-from-list>"}'
```
