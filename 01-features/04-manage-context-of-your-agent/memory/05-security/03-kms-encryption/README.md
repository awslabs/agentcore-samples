# Customer-managed KMS encryption

By default, AgentCore Memory encrypts data with AWS-owned keys. Pass `encryptionKeyArn` to `CreateMemory` to use a customer-managed key (CMK) instead.

## What you learn

- Pass `encryptionKeyArn` to `CreateMemory`
- The IAM permissions the memory execution role needs on the key
- Verify the key is in use via `GetMemory`

## Run

```bash
export KMS_KEY_ARN=arn:aws:kms:us-east-1:111122223333:key/abcd-...
export MEMORY_EXECUTION_ROLE_ARN=arn:aws:iam::111122223333:role/AgentCoreMemoryRole
python kms-encryption.py
```

## When to use a CMK

- **Regulatory or contractual** requirement for customer-controlled key material.
- **Key rotation control** — you set the rotation cadence rather than relying on AWS-owned keys.
- **Cross-account auditability** — CloudTrail records `kms:GenerateDataKey` and `kms:Decrypt` calls against your key.
- **Revocation** — disabling the key immediately blocks reads/writes on the memory resource.

## IAM and key policy

The memory execution role needs at least:

```json
{
  "Effect": "Allow",
  "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
  "Resource": "arn:aws:kms:<region>:<account>:key/<key-id>"
}
```

The KMS key policy must in turn allow that role principal. If the key is in a different account, set up cross-account `kms:CreateGrant` or include the role in the key's policy.

## Best practices

- **One CMK per memory resource (or a small group of related ones).** Per-tenant keys are the cleanest revocation story.
- **Don't reuse a Kinesis stream key as the memory CMK** unless deliberate — different blast radius. Streams have their own `kms:GenerateDataKey` permission requirement.
- **Enable automatic key rotation** unless you have a reason not to. Rotation is transparent to memory operations.
- **Alarm on `kms:Decrypt` failures** in CloudTrail — they often appear as `StreamUserError` or extraction failures with no other obvious symptom.
- **Test revocation in a staging account.** Disabling the key should fail reads cleanly; if it doesn't, your CMK isn't actually in the read path.

## AWS CLI walkthrough

The same flow expressed with the AWS CLI:

```bash
# Prereqs:
#   - A KMS CMK in the same region as the memory.
#   - A memory execution role whose trust policy allows
#     bedrock-agentcore.amazonaws.com to assume it, and whose permissions
#     include kms:GenerateDataKey and kms:Decrypt on the key.
#   - The key policy must allow that role to use the key.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export KMS_KEY_ARN="arn:aws:kms:$AWS_REGION:$ACCOUNT_ID:key/<your-cmk-key-id>"
export MEMORY_EXECUTION_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/AgentCoreMemoryRole"

# 1. Create memory with CMK encryption
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "KMSCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --encryption-key-arn "$KMS_KEY_ARN" \
  --memory-execution-role-arn "$MEMORY_EXECUTION_ROLE_ARN" \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Verify the key is recorded on the resource
aws bedrock-agentcore-control get-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --query 'memory.{status:status,key:encryptionKeyArn}'

# 3. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
