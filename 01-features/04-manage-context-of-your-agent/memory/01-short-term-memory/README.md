# Short-term memory

Short-term memory stores raw events scoped to an `actorId` and a `sessionId`. It gives the agent immediate, low-latency access to the current interaction — no extraction, no embeddings, no background pipeline.

Events are usually conversation turns, but an event's payload is a list of items that can also carry structured application data (`json`) or arbitrary data (`blob`) — see [`05-payload-types/`](./05-payload-types/).

## Standard usage

[`standard-usage.py`](./standard-usage.py) is the canonical flow: create a memory resource, wait for `ACTIVE`, append a few events with `CreateEvent`, list them back with `ListEvents`, fetch one with `GetEvent`, delete the memory.

```bash
pip install boto3 bedrock-agentcore
python standard-usage.py boto3   # default — direct service calls
python standard-usage.py sdk     # AgentCore MemoryClient helpers
```

Every sub-feature script supports the same three surfaces.

## Sub-features

| # | Folder | What it teaches |
|---|---|---|
| 01 | [`01-events-and-sessions/`](./01-events-and-sessions/) | The CreateEvent / ListEvents / GetEvent / ListSessions loop |
| 02 | [`02-event-metadata/`](./02-event-metadata/) | Tagging events with metadata and filtering with `EQUALS_TO`, `EXISTS`, `NOT_EXISTS` |
| 03 | [`03-actor-session-isolation/`](./03-actor-session-isolation/) | One memory resource, many actors, no cross-actor leakage |
| 04 | [`04-branching/`](./04-branching/) | Forking a session for what-if flows or parallel sub-agents |
| 05 | [`05-payload-types/`](./05-payload-types/) | `conversational`, `json`, and `blob` payload items — and which ones extraction reads |

## Examples

Framework integrations live under [`examples/`](./examples/). They wire short-term memory into Strands, LangGraph, and LlamaIndex agents using three common patterns:

| Pattern | What it is | When to use |
|---|---|---|
| **Built-in hook** | Framework's out-of-the-box AgentCore memory adapter | Fastest path; standard save/retrieve lifecycle |
| **Custom hook** | Your own hook implementation | Conditional logic, custom retrieval, orchestration |
| **memory-as-tool** | Memory operations exposed as tools the LLM calls | Agent decides when to recall/save |

Every framework has an adapter for that first row, under a different name
(`AgentCoreMemorySessionManager` in Strands, `AgentCoreMemorySaver` in LangGraph,
`AgentCoreMemory` in LlamaIndex) — see
[06-usage-patterns.md](../00-getting-started/06-usage-patterns.md). The hook and tool patterns
are not alternatives to it; they're how **long-term** memory gets layered on top, in
[`../02-long-term-memory/examples/`](../02-long-term-memory/examples/).

| Subtree | Contents |
|---|---|
| [`examples/single-agent/with-strands-agent/`](./examples/single-agent/with-strands-agent/) | Built-in hook + custom hook; travel-planning branching example |
| [`examples/single-agent/with-langgraph-agent/`](./examples/single-agent/with-langgraph-agent/) | `AgentCoreMemorySaver` checkpointer (`math-agent-with-checkpointing`), memory-as-tool (`personal-fitness-coach`), checkpointed human-in-the-loop (`support-agent-human-in-the-loop`) |
| [`examples/single-agent/with-llamaindex-agent/`](./examples/single-agent/with-llamaindex-agent/) | Four domain examples: academic research, investment, legal, medical |
| [`examples/multi-agent/with-strands-agent/`](./examples/multi-agent/with-strands-agent/) | Multi-agent travel planner; `multi-agent-parallel-branches/` shows branch-per-subagent |

## Best practices

- **One conversation = one `sessionId`.** Don't reuse a session across days; start a new one and re-hydrate context with `ListEvents` if needed.
- **Pick a stable `actorId`** that maps to the real user (Cognito `sub`, internal user id) — long-term memory namespaces typically template on it.
- **Use `includePayloads=False`** on `ListEvents` when you only need ids/timestamps; switch to `True` only when you need the content.
- **Cap storage cost with `eventExpiryDuration`** (3–365 days) at memory creation.
- **Don't put sensitive data in event metadata** — metadata is not encrypted with your customer-managed KMS key.
- **Write app events as `json` payloads, not as narrated turns.** Structured behaviour (filters applied, items viewed) extracts as well as speech does, and the transcript stays an honest record of what was actually said.
- **Remember `blob` is short-term only.** It is stored and readable, but no strategy extracts it — use `json` for anything that should reach long-term memory.
- **Plan branches before you need them.** Decide branch names up front so `ListEvents` filters stay clean.

## Where to go next

- Cross-session persistence: [`../02-long-term-memory/`](../02-long-term-memory/)
- Security and isolation: [`../05-security/`](../05-security/)
- Wire memory into runtime/identity/Guardrails: [`../03-integrations/`](../03-integrations/)

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create a memory resource and capture its id. `--name` accepts letters, digits
#    and underscores only (pattern [a-zA-Z][a-zA-Z0-9_]{0,47}), so no hyphens.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" \
  --name "StmStandardCli_$(date +%s)" \
  --event-expiry-duration 30 \
  --client-token "$(uuidgen)" \
  --query 'memory.id' --output text)

# 2. Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
#    and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 3. Append three events
for i in 1 2 3; do
  aws bedrock-agentcore create-event \
    --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" \
    --actor-id user-42 \
    --session-id sess-cli \
    --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --payload "[{\"conversational\":{\"role\":\"USER\",\"content\":{\"text\":\"turn $i\"}}}]"
done

# 4. List events for the session
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id user-42 \
  --session-id sess-cli \
  --include-payloads

# 5. Fetch a single event by id. Take the first id from step 4 rather than pasting
#    one by hand. --no-paginate keeps --query on one page, so the result is just the
#    id (--max-items would append the null NextToken as "None").
EVENT_ID=$(aws bedrock-agentcore list-events \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id user-42 \
  --session-id sess-cli \
  --no-paginate \
  --query 'events[0].eventId' --output text)
aws bedrock-agentcore get-event \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id user-42 \
  --session-id sess-cli \
  --event-id "$EVENT_ID"

# 6. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --client-token "$(uuidgen)"
```
