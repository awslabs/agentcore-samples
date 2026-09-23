# Long-term memory

Long-term memory turns raw conversation events into structured, reusable records — facts, summaries, preferences, and episodes — organised in namespaces and retrieved by semantic search.

Run the canonical end-to-end flow first:

```bash
pip install boto3 bedrock-agentcore
python standard-usage.py boto3   # default — direct service calls
python standard-usage.py sdk     # AgentCore MemoryClient helpers
```

It creates a memory with a semantic strategy, sends a few `CreateEvent` calls, waits for extraction, and retrieves the resulting records. Every sub-feature script supports the same three surfaces.

## Sub-features

| Folder | What it covers |
|---|---|
| [`01-built-in-strategies/`](./01-built-in-strategies/) | The four built-in extractors: semantic, summary, user preference, episodic |
| [`02-strategy-overrides/`](./02-strategy-overrides/) | Customise built-in extraction and consolidation prompts |
| [`03-self-managed-strategy/`](./03-self-managed-strategy/) | Plug your own extraction worker via SNS + S3 with message/token/time triggers |
| [`04-namespaces/`](./04-namespaces/) | Template variables, flexible namespaces (`namespaceKeys`), exact vs. prefix matching, multi-tenancy |
| [`05-retrieval/`](./05-retrieval/) | `RetrieveMemoryRecords`, `ListMemoryRecords`, `GetMemoryRecord` |
| [`06-record-metadata/`](./06-record-metadata/) | `indexedKeys`, structured metadata, `metadataFilters` |
| [`07-skip-STM/`](./07-skip-STM/) | Write LTM without a short-term event: using `IngestData` API and direct CRUD with `BatchCreate/Update/DeleteMemoryRecords` |
| [`08-manage-extraction/`](./08-manage-extraction/) | Skip extraction (`extractionMode=SKIP`) and redrive failed jobs |
| [`09-record-streaming/`](./09-record-streaming/) | Push lifecycle events to Kinesis for event-driven pipelines |

## The four built-in strategies

| Strategy | Extracts | Typical namespace |
|---|---|---|
| **Semantic** | Standalone facts about the world | `/users/{actorId}/facts/` |
| **Summary** | Rolling conversation summaries | `/sessions/{sessionId}/summary/` |
| **User Preference** | Stable per-user settings | `/users/{actorId}/preferences/` |
| **Episodic** | Meaningful interaction sequences | `/episodes/{actorId}/` |

Built-in strategies can be **overridden** to swap their extraction or consolidation prompts (`02-strategy-overrides/`), or replaced entirely with a **self-managed** worker (`03-self-managed-strategy/`).

All four extract from `json` payload items as readily as from conversation turns, so behavioural signal the user never spoke aloud still becomes long-term memory — see [`../01-short-term-memory/02-payload-types/`](../01-short-term-memory/02-payload-types/). `blob` payloads are never extracted.

## Framework integrations

End-to-end agent examples live under `examples/`:

- [`examples/single-agent/`](./examples/single-agent/) — Strands, LangGraph, LlamaIndex with hooks, callbacks, memory-block and memory-as-tool patterns.
- [`examples/multi-agent/`](./examples/multi-agent/) — Multi-agent flows with shared LTM (travel booking, healthcare).

## Best practices

- **Pick a namespace template before you write.** `{actorId}` and `{sessionId}` are the two stable hooks; everything else flows from them.
- **Wait for extraction.** Long-term records appear ~30–60 seconds after `CreateEvent`. Don't retrieve immediately.
- **Pin retrieval to a namespace.** Cross-namespace search is rarely what you want and pulls noise.
- **Use `metadataFilters` for hard constraints** (region, tier, language) — they're enforced at the index, not in the prompt.
- **Subscribe to streaming for downstream pipelines.** Polling for changes is the wrong default at any non-trivial scale.
- **Redrive failed jobs deliberately.** Read `failureReason` first; blind retries cost tokens and rarely help.

## Where to next

- Observability for memory operations: [`../04-observability/`](../04-observability/)
- IAM, encryption, multi-tenant isolation: [`../05-security/`](../05-security/)
- Streaming use cases (cross-region, recommendations, analytics): [`./09-record-streaming/examples/`](./09-record-streaming/examples/)

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory with a semantic strategy. Namespaces use {actorId}/{sessionId} templates.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "LtmStandardCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-strategies '[{
    "semanticMemoryStrategy": {
      "name": "UserFacts",
      "namespaces": ["/users/{actorId}/facts/"]
    }
  }]' \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Drive a few conversation turns; extraction happens asynchronously.
for line in \
  '{"role":"USER","text":"I prefer Python and I'\''m based in Berlin."}' \
  '{"role":"ASSISTANT","text":"Got it."}' \
  '{"role":"USER","text":"I'\''m allergic to peanuts."}'; do
  role=$(echo "$line" | jq -r .role)
  text=$(echo "$line" | jq -r .text)
  aws bedrock-agentcore create-event \
    --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
    --actor-id user-42 --session-id sess-cli \
    --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --payload "[{\"conversational\":{\"role\":\"$role\",\"content\":{\"text\":\"$text\"}}}]"
done

# 3. Wait ~60s, then retrieve records.
sleep 60
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/user-42/facts/" \
  --search-criteria '{"searchQuery":"preferences and constraints?","topK":5}'

# 4. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
