# Self-managed memory strategy

You own the extraction pipeline end-to-end. AgentCore handles only storage and retrieval; the *what to extract* and *how to consolidate* logic lives in your code.

Use this when:

- You need a record schema that built-in strategies don't produce.
- You want to use a non-Bedrock model, or your own fine-tuned model.
- You must pull additional context from external systems (CRM, EHR, ticket system) before deciding what to write.

## How it works

1. You configure `selfManagedConfiguration` with a payload S3 bucket, an SNS topic, and trigger conditions.
2. As `CreateEvent` calls accumulate, AgentCore evaluates the triggers. When one fires, AgentCore:
   - Writes the conversation payload to your S3 bucket.
   - Publishes a notification to your SNS topic.
3. Your subscriber (Lambda, ECS task, etc.) reads the payload from S3, runs your extraction logic, and persists the resulting records via `BatchCreateMemoryRecords` / `BatchUpdateMemoryRecords` / `BatchDeleteMemoryRecords`.

## Trigger conditions

| Trigger | When it fires |
|---|---|
| `messageBasedTrigger.messageCount` | After N messages (1–50) |
| `tokenBasedTrigger.tokenCount` | After cumulative token count |
| `timeBasedTrigger.idleSessionTimeout` | After N seconds idle (10–3000) |

You may set multiple triggers — the strategy fires when any matches.

## Run

```bash
pip install boto3 bedrock-agentcore
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export MEMORY_EXECUTION_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/<your-memory-execution-role>"
export PAYLOAD_BUCKET=my-agentcore-payload-bucket
export TOPIC_ARN="arn:aws:sns:$AWS_REGION:$ACCOUNT_ID:agentcore-self-managed"
python self-managed-strategy.py boto3   # default — direct service calls
python self-managed-strategy.py sdk     # documents the SDK gap (no selfManagedConfiguration helper)
```

The execution role must allow:

- `s3:PutObject` and `s3:GetBucketLocation` on `arn:aws:s3:::$PAYLOAD_BUCKET` and `arn:aws:s3:::$PAYLOAD_BUCKET/*`
- `sns:Publish` and `sns:GetTopicAttributes` on `$TOPIC_ARN`
- be assumable by `bedrock-agentcore.amazonaws.com` with condition `ArnLike: aws:SourceArn: arn:aws:bedrock-agentcore:<region>:<account>:memory/*`

A working extraction subscriber (Lambda) is provided under [`../examples/single-agent/with-strands-agent/02-custom-hook/culinary-assistant-self-managed-strategy/lambda_function.py`](../examples/single-agent/with-strands-agent/02-custom-hook/culinary-assistant-self-managed-strategy/lambda_function.py).

## Best practices

- **Tune triggers to your workload.** A chat agent may want `messageCount=10`; a long-form journaling agent may want `idleSessionTimeout=300`. Triggers fire often → cost; rarely → stale memory.
- **Keep extraction idempotent.** Triggers may overlap; design the subscriber so re-running it on the same window doesn't duplicate records (use `requestIdentifier` on `BatchCreateMemoryRecords`).
- **Use `historicalContextWindowSize`** (0–50) to control how much prior conversation AgentCore includes in the delivered payload.
- **Validate before writing.** Run a guardrail step on extracted text before persisting — it's the last point you control.
- **Watch CloudWatch metrics** under `AWS/Bedrock-AgentCore` for trigger invocations and consolidation latency.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create the memory with a self-managed strategy. The role must allow
#    PutObject + GetBucketLocation to the bucket and Publish + GetTopicAttributes to the topic.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "SelfManagedCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-execution-role-arn "$MEMORY_EXECUTION_ROLE_ARN" \
  --memory-strategies "[{
    \"customMemoryStrategy\": {
      \"name\": \"MyOwnExtractor\",
      \"description\": \"Custom extraction owned by my Lambda\",
      \"configuration\": {
        \"selfManagedConfiguration\": {
          \"invocationConfiguration\": {
            \"payloadDeliveryBucketName\": \"$PAYLOAD_BUCKET\",
            \"topicArn\": \"$TOPIC_ARN\"
          },
          \"historicalContextWindowSize\": 10,
          \"triggerConditions\": [
            {\"messageBasedTrigger\": {\"messageCount\": 6}},
            {\"tokenBasedTrigger\": {\"tokenCount\": 4000}},
            {\"timeBasedTrigger\": {\"idleSessionTimeout\": 300}}
          ]
        }
      }
    }
  }]" \
  --query 'memory.id' --output text)

# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Send events; AgentCore drops payloads to S3 and publishes to SNS.
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-alex --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"hello"}}}]'

# 3. Your subscriber writes records back via batch APIs.
#    Each record requires: requestIdentifier, namespaces (array), content, timestamp, memoryStrategyId.
aws bedrock-agentcore batch-create-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --records '[{
    "requestIdentifier":"unique-id-1",
    "namespaces":["/users/user-alex/custom/"],
    "content":{"text":"User likes Python"},
    "timestamp":1785424000,
    "memoryStrategyId":"<strategy-id-from-create-memory-response>"
  }]'

# 4. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
