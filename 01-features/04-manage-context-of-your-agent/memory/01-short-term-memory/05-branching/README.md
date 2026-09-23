# Event branching

Branches let you fork a session at a chosen event and continue down divergent paths. Each branch is identified by a `name` and a `rootEventId` (the parent event it forks from). Branches share the parent's history but record their own subsequent events.

## What you learn

- Forking a session by setting `branch={"name": ..., "rootEventId": ...}` on `CreateEvent`
- Reading one branch only with `filter={"branch": {"name": "...", "includeParentBranches": False}}`
- Reading the full ancestry with `includeParentBranches=True`

## When to use

- **What-if conversations** — explore alternative replies without polluting the canonical thread.
- **Parallel sub-agents** — each subagent writes on its own branch off a shared parent state, then a coordinator stitches the branches.
- **A/B exploration during development** — compare retrieval/strategy variants on the same upstream context.

## Run

```bash
python event-branching.py boto3   # default — direct service calls
python event-branching.py sdk     # uses MemoryClient.fork_conversation
```

## Best practices

- **Pick the fork point deliberately.** The `rootEventId` becomes the shared base — everything before it is parent context for every descendant branch.
- **Use short, descriptive branch names** (`autumn`, `agent-a`, `experiment-1`). The name is opaque to AgentCore but is your filter key on `ListEvents`.
- For multi-agent parallel work, share the parent context but isolate per-agent contributions on distinct branches; merge by reading each branch separately.
- See `../examples/multi-agent/with-strands-agent/multi-agent-parallel-branches/` for an end-to-end Strands example.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory and wait until ACTIVE. `--name` accepts letters, digits and
#    underscores only (pattern [a-zA-Z][a-zA-Z0-9_]{0,47}), so no hyphens.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "BranchingCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --query 'memory.id' --output text)

# CreateEvent is rejected while the memory is CREATING, so wait it out. Takes a
# couple of minutes. The loop also exits on FAILED, so it can't hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Seed two events on the root branch. Capture the second event id as the fork point.
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"Trip to Lisbon."}}}]'

FORK_EVENT_ID=$(aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"ASSISTANT","content":{"text":"When?"}}}]' \
  --query 'event.eventId' --output text)

# 3. Append two events on a new "autumn" branch rooted at FORK_EVENT_ID.
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --branch "{\"name\":\"autumn\",\"rootEventId\":\"$FORK_EVENT_ID\"}" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"October."}}}]'

# 4. Same fork point, different branch name = a parallel "winter" thread.
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --branch "{\"name\":\"winter\",\"rootEventId\":\"$FORK_EVENT_ID\"}" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"December instead?"}}}]'

# 5. Read one branch in isolation
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli --include-payloads \
  --filter '{"branch":{"name":"autumn","includeParentBranches":false}}'

# 6. Read a branch with all its parent context
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-42 --session-id sess-cli --include-payloads \
  --filter '{"branch":{"name":"winter","includeParentBranches":true}}'

# 7. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
