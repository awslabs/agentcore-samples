# Built-in strategies with prompt overrides

Built-in overrides keep the AgentCore-managed extraction pipeline but let you customise:

- the **prompt instructions** for any pipeline step (extraction, consolidation, reflection)
- the **Bedrock model** used for that step

The output **schema is fixed** — only instructions and model are override-able. If you need to change the schema, use a [self-managed strategy](../03-self-managed-strategy/) instead.

## What you learn

- Configure `customMemoryStrategy` with one of `semanticOverride`, `summaryOverride`, `userPreferenceOverride`, `episodicOverride`
- Required: `memoryExecutionRoleArn` — Bedrock invocations bill against your account
- Each step (`extraction` / `consolidation` / `reflection`) takes `appendToPrompt` and `modelId`

## Run

```bash
pip install boto3 bedrock-agentcore
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export MEMORY_EXECUTION_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/<your-memory-execution-role>"
python strategies-with-overrides.py boto3   # default — direct service calls
python strategies-with-overrides.py sdk     # uses MemoryClient.gmcp_client (helper doesn't expose memoryExecutionRoleArn)
```

The role's trust policy must allow `bedrock-agentcore.amazonaws.com` to assume it, with `bedrock:InvokeModel` permission for the chosen model.

## Best practices

- **Be additive, not contradictory.** `appendToPrompt` is added to the system prompt — write instructions that *narrow* or *clarify* the built-in behaviour, not contradict it.
- **Pick a model that matches the workload.** Sonnet for nuanced extraction, Haiku for high-volume / low-margin extraction.
- **Test with realistic conversations.** Override behaviour is best validated by feeding it the actual transcripts you'll see in production.
- **Don't overfit.** If your schema needs are different, drop overrides and write a self-managed strategy — overrides cannot change the record shape.
- **Cost note.** Override Bedrock invocations bill against your account separately from AgentCore charges.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# 1. Create memory with a customMemoryStrategy that wraps a semantic override.
#    The execution role lets AgentCore invoke Bedrock on your behalf.
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "OverrideCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-execution-role-arn "$MEMORY_EXECUTION_ROLE_ARN" \
  --memory-strategies '[{
    "customMemoryStrategy": {
      "name": "MedicalFacts",
      "description": "Health-only semantic extraction",
      "namespaces": ["/users/{actorId}/medical-facts/"],
      "configuration": {
        "semanticOverride": {
          "extraction": {
            "appendToPrompt": "Focus only on health-related facts.",
            "modelId": "global.anthropic.claude-opus-4-6-v1"
          },
          "consolidation": {
            "appendToPrompt": "Prefer the more recent record on conflict.",
            "modelId": "global.anthropic.claude-opus-4-6-v1"
          }
        }
      }
    }
  }]' \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Drive the conversation and wait
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id user-alex --session-id sess-cli \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"I take metformin daily."}}}]'
sleep 75

# 3. Retrieve
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/user-alex/medical-facts/" \
  --search-criteria '{"searchQuery":"medical history","topK":10}'

# 4. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
