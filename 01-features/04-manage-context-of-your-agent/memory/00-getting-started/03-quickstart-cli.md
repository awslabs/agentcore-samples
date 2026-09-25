# Quickstart — AWS CLI

End-to-end walkthrough of AgentCore Memory using only `aws bedrock-agentcore-control` (control plane) and `aws bedrock-agentcore` (data plane) commands. The [boto3](./04-quickstart-boto3.py) and [AgentCore SDK](./05-quickstart-agentcore-sdk.py) quickstarts cover the same flow.

## Prerequisites

- AWS CLI v2 installed and configured with credentials for a region where AgentCore Memory is available (e.g., `us-east-1`).
- An IAM **memory execution role ARN** that AgentCore can assume to read events and emit long-term records. See the [execution role docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-execution-role.html).
- Amazon Bedrock access to the embedding model used by semantic strategies (default: `amazon.titan-embed-text-v2:0`).

Set shell variables used throughout:

```bash
export AWS_REGION=us-east-1   # use any AgentCore-supported region
export MEMORY_ROLE_ARN=arn:aws:iam::<account-id>:role/AgentCoreMemoryExecutionRole
export ACTOR_ID=user-42
export SESSION_ID=sess-$(date +%s)
```

## 1. Create a memory resource

```bash
aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" \
  --name QuickstartMemory \
  --description "Getting-started memory resource" \
  --event-expiry-duration 30 \
  --memory-execution-role-arn "$MEMORY_ROLE_ARN" \
  --client-token "$(uuidgen)"
```

Capture the returned `memory.id` (format: `<Name>-<suffix>`):

```bash
export MEMORY_ID=QuickstartMemory-xxxxxxxxxx
```

Poll until the resource reaches `ACTIVE`:

```bash
aws bedrock-agentcore-control get-memory \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --query 'memory.status'
```

## 2. Write a short-term event

```bash
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id "$ACTOR_ID" \
  --session-id "$SESSION_ID" \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[
    {"conversational":{"role":"USER","content":{"text":"My name is Alex and I prefer Python."}}},
    {"conversational":{"role":"ASSISTANT","content":{"text":"Nice to meet you, Alex."}}}
  ]'
```

## 3. Read events back

List events for the session:

```bash
aws bedrock-agentcore list-events \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id "$ACTOR_ID" \
  --session-id "$SESSION_ID"
```

Fetch a single event by ID:

```bash
# Take the first id from the list above rather than pasting one by hand.
# --no-paginate keeps --query on one page, so the result is just the id.
EVENT_ID=$(aws bedrock-agentcore list-events \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id "$ACTOR_ID" \
  --session-id "$SESSION_ID" \
  --no-paginate \
  --query 'events[0].eventId' --output text)

aws bedrock-agentcore get-event \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --actor-id "$ACTOR_ID" \
  --session-id "$SESSION_ID" \
  --event-id "$EVENT_ID"
```

## 4. Add a built-in semantic strategy

Long-term records are produced by a strategy attached to the memory resource. This adds the built-in semantic strategy with an actor-scoped namespace:

```bash
aws bedrock-agentcore-control update-memory \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --client-token "$(uuidgen)" \
  --memory-strategies '{
    "addMemoryStrategies": [{
      "semanticMemoryStrategy": {
        "name": "UserFacts",
        "namespaceTemplates": ["/users/{actorId}/facts/"]
      }
    }]
  }'
```

Extraction runs asynchronously. Wait ~60 seconds before retrieving.

## 5. Retrieve a memory record

```bash
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --namespace "/users/$ACTOR_ID/facts" \
  --search-criteria '{"searchQuery":"What programming language does the user prefer?","topK":3}'
```

You should see a record containing "Alex" and "Python".

## 6. Teardown

```bash
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" \
  --client-token "$(uuidgen)"
```

## See also

- [Concepts](./01-memory-concepts.md)
- Same flow in [boto3](./04-quickstart-boto3.py) and [AgentCore SDK](./05-quickstart-agentcore-sdk.py).
