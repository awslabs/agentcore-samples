# Retrieving long-term memory records

Three retrieval primitives, picked by the question you're asking:

| API | Use when |
|---|---|
| `RetrieveMemoryRecords` | Relevance-ranked semantic search within a namespace (the common case) |
| `ListMemoryRecords` | You want to browse / enumerate every record in a namespace |
| `GetMemoryRecord` | You already have a `memoryRecordId` and want the full record |

`RetrieveMemoryRecords` accepts either:

- `namespace=` — exact namespace match.
- `namespacePath=` — prefix-style match across a namespace hierarchy.

…plus a `searchCriteria` object with `searchQuery`, `topK` (default 20, max 100), `metadataFilters`, and optional `memoryStrategyId` to constrain to a single strategy.

## Run

```bash
pip install boto3 bedrock-agentcore
python retrieve-records-and-citations.py boto3   # default — direct service calls
python retrieve-records-and-citations.py sdk     # AgentCore MemoryClient helpers (uses gmcp_client for list/get)
```

## What's in a result

Each hit returns:

- `memoryRecordId`
- `content.text` — the extracted record
- `score` — relevance, only on `RetrieveMemoryRecords`
- `namespaces` — the namespaces this record was written to
- `memoryStrategyId` — which strategy produced it
- `metadata`, `createdAt`

## Best practices

- **Always pin a namespace.** Unscoped retrieval across all records is rarely what you want and pulls noise.
- **Pick `topK` to fit the LLM context budget.** Pull 3–5 for direct injection; pull more only if you re-rank or filter downstream.
- **Use `metadataFilters` for hard constraints** (`region=EU`, `tier=premium`) — they're enforced at the index, unlike the LLM-side filtering you'd otherwise do.
- **Prefer `namespacePath=` for hierarchical reads** (e.g. all preferences for an actor across strategies) and `namespace=` for exact targeting.
- **Pair with citations.** Surface the namespace + record id when an answer relies on memory — useful for debugging hallucinated extractions.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory + extract a few facts first (see standard-usage.py for setup).
#    It prints the memoryId when it finishes — paste that id here. Quote it, so an
#    unreplaced placeholder fails on the API call instead of as a shell redirect.
export MEMORY_ID="<the memoryId printed by standard-usage.py>"

# 2. Semantic retrieval — relevance-ranked
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/user-alex/facts/" \
  --search-criteria '{"searchQuery":"dietary restrictions","topK":5}'

# 3. ListMemoryRecords — enumerate every record in a namespace
aws bedrock-agentcore list-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/user-alex/facts/"

# 4. GetMemoryRecord — fetch one record in full. Take the first id from the list
#    above rather than pasting one by hand.
RECORD_ID=$(aws bedrock-agentcore list-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/user-alex/facts/" --no-paginate \
  --query 'memoryRecordSummaries[0].memoryRecordId' --output text)
aws bedrock-agentcore get-memory-record \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --memory-record-id "$RECORD_ID"

# 5. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
