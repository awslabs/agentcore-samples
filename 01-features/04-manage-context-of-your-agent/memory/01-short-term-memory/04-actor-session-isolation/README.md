# Actor and session isolation

`actorId` + `sessionId` are the only required scoping keys for short-term memory. A single memory resource can serve every user in your application — events are isolated by these two keys at the API level, and IAM conditions on `bedrock-agentcore:actorId` / `sessionId` enforce isolation across roles.

## What you learn

- One memory resource serves many actors
- Events are addressable only via `(memoryId, actorId, sessionId)`
- `ListSessions` returns sessions for the requested actor only

## Run

```bash
python actor-session-isolation.py boto3   # default — direct service calls
python actor-session-isolation.py sdk     # AgentCore MemoryClient helpers
```

## Best practices

- **Pick a stable `actorId`** that maps to a real principal (Cognito `sub`, internal user id) — not a transient device id. Long-term memory namespaces typically use `{actorId}`, so a stable id keeps a user's records together.
- **Enforce isolation at IAM, not just at the application layer.** Use the `bedrock-agentcore:actorId` and `bedrock-agentcore:sessionId` condition keys on memory actions so a runtime role can only touch one user's data. See `../../05-security/01-iam-scoped-access/`.
- For multi-tenant workloads, prefix actor ids with the tenant (`acme/user-42`) and condition IAM on the prefix.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# Single memory resource serves many actors. Events are scoped by (actorId, sessionId).

# 1. Create the memory and capture its id. `--name` accepts letters, digits and
#    underscores only (pattern [a-zA-Z][a-zA-Z0-9_]{0,47}), so no hyphens.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "ActorIsoCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --query 'memory.id' --output text)

# 2. Wait until ACTIVE — CreateEvent is rejected while the memory is CREATING.
#    Takes a couple of minutes. The loop also exits on FAILED, so it can't hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 3. Two actors, two sessions
for actor in alice bob; do
  aws bedrock-agentcore create-event \
    --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
    --actor-id "$actor" --session-id "${actor}-session" \
    --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --payload "[{\"conversational\":{\"role\":\"USER\",\"content\":{\"text\":\"hello from $actor\"}}}]"
done

# 4. Each actor only sees their own events
aws bedrock-agentcore list-events --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" --actor-id alice --session-id alice-session
aws bedrock-agentcore list-events --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" --actor-id bob --session-id bob-session

# 5. ListSessions is also actor-scoped
aws bedrock-agentcore list-sessions --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" --actor-id alice
aws bedrock-agentcore list-sessions --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" --actor-id bob

# 6. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
