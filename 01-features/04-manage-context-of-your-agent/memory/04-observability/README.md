# Observability

AgentCore Memory emits CloudWatch metrics and logs to your account so you can monitor data-plane usage, ingestion health, and stream delivery.

## What you learn

- Read CloudWatch metrics under the `AWS/Bedrock-AgentCore` namespace
- Alarm on stream publishing failures
- Tail the per-memory ingestion log group when log delivery is enabled

## Run

```bash
export MEMORY_ARN=arn:aws:bedrock-agentcore:us-east-1:111122223333:memory/mem-abc
python observability.py
```

## What's emitted

### Data-plane metrics

`Invocations`, `Latency`, `Errors` for each data-plane operation (`CreateEvent`, `RetrieveMemoryRecords`, etc.) — scoped per memory resource.

### Ingestion metrics

`Invocations`, `Latency`, `Errors`, `NumberOfMemoryRecords` for extraction and consolidation — scoped per memory resource and strategy.

### Streaming metrics

| Metric | Meaning |
|---|---|
| `StreamPublishingSuccess` | Events successfully published to your Kinesis stream |
| `StreamPublishingFailure` | Events that failed to publish (transient + terminal) |
| `StreamUserError` | Failures caused by config issues (IAM, KMS key state) |

All three are emitted as `Count` units with dimensions `Operation=MemoryStreamEvent` and `Resource=<memory ARN>`.

### Logs

When log delivery is enabled on the memory resource, ingestion errors land in `/aws/bedrock-agentcore/memory/<memoryId>`. Streaming terminal failures include `streamArn`, `errorCode`, `errorMessage`, `eventType`, and `memoryRecordId` fields.

## Best practices

- **Alarm on `StreamPublishingFailure` and `StreamUserError`.** Treat user errors as page-worthy — they almost always mean broken IAM or KMS.
- **Watch `Errors` on `RetrieveMemoryRecords`.** A spike usually means a strategy was deleted or a namespace renamed.
- **Track `NumberOfMemoryRecords` per strategy.** A sudden drop is the canary for an extraction regression.
- **Enable log delivery in production.** Without it, ingestion failures are invisible — metrics will tell you something broke, only logs tell you what.
- **Pair alarms with a runbook.** Streaming failures usually want a redrive on the affected events; user errors want an IAM fix.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# CloudWatch metrics: namespace AWS/Bedrock-AgentCore.
# Streaming health is dimensioned by Operation=MemoryStreamEvent + Resource=<memory ARN>.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export MEMORY_ARN="arn:aws:bedrock-agentcore:$AWS_REGION:$ACCOUNT_ID:memory/<your-memory-id>"

# 1. Sum streaming successes over the last hour
aws cloudwatch get-metric-statistics --region "$AWS_REGION" \
  --namespace "AWS/Bedrock-AgentCore" --metric-name "StreamPublishingSuccess" \
  --dimensions Name=Operation,Value=MemoryStreamEvent Name=Resource,Value="$MEMORY_ARN" \
  --statistics Sum --period 300 \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time   "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 2. Sum streaming failures (alarm on this in production)
aws cloudwatch get-metric-statistics --region "$AWS_REGION" \
  --namespace "AWS/Bedrock-AgentCore" --metric-name "StreamPublishingFailure" \
  --dimensions Name=Operation,Value=MemoryStreamEvent Name=Resource,Value="$MEMORY_ARN" \
  --statistics Sum --period 300 \
  --start-time "$(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time   "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 3. Alarm on any failure in a 5-minute window
aws cloudwatch put-metric-alarm --region "$AWS_REGION" \
  --alarm-name "AgentCoreMemory-StreamFailure" \
  --namespace "AWS/Bedrock-AgentCore" --metric-name "StreamPublishingFailure" \
  --dimensions Name=Operation,Value=MemoryStreamEvent Name=Resource,Value="$MEMORY_ARN" \
  --statistic Sum --period 300 --evaluation-periods 1 \
  --threshold 0 --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions "$SNS_TOPIC_ARN"

# 4. Tail ingestion logs (log group format: /aws/bedrock-agentcore/memory/<memoryId>)
MEMORY_ID="${MEMORY_ARN##*/}"
aws logs tail "/aws/bedrock-agentcore/memory/$MEMORY_ID" \
  --region "$AWS_REGION" --since 30m --follow
```
