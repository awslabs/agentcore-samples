# Production deployment checklist

Work top to bottom before you put an AgentCore Memory–backed agent in front of users. Each section is actionable; the deep-dives live under [`../05-security/`](../05-security/) and [`../04-observability/`](../04-observability/).

## 1. IAM least-privilege

Grant the runtime role only the data-plane actions it actually calls, and scope them with conditions. Two distinct principals to get right:

- **Your runtime/agent role** — calls the *data plane* (`bedrock-agentcore:*` on events and records).
- **The memory execution role** (`memoryExecutionRoleArn` on the resource) — assumed by the *service* to invoke Bedrock for built-in strategy extraction. Required for built-in strategies because the model bills to your account.

### Runtime role — scoped data-plane policy

Grant only the operations the agent uses. A read-mostly agent that records turns and retrieves records needs roughly:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "MemoryDataPlaneScopedToActor",
      "Effect": "Allow",
      "Action": [
        "bedrock-agentcore:CreateEvent",
        "bedrock-agentcore:ListEvents",
        "bedrock-agentcore:RetrieveMemoryRecords"
      ],
      "Resource": "arn:aws:bedrock-agentcore:us-east-1:111122223333:memory/mem-abc123",
      "Condition": {
        "StringEquals": { "bedrock-agentcore:actorId": "${aws:PrincipalTag/actorId}" }
      }
    }
  ]
}
```

Principles:
- **Enumerate actions; never `bedrock-agentcore:*`.** Add batch CRUD only if the agent actually does self-managed writes/pruning. Keep control-plane actions (`CreateMemory`, `DeleteMemory`) out of the runtime role entirely — provision and tear down memories from a separate, privileged path.
- **Scope `Resource` to the specific memory ARN(s).** Wildcards across all memories defeat tenant isolation.
- **Condition on `actorId`** (and namespace where supported) so one user's credentials can't read another's memory. This is the actual access boundary — namespaces are organization, not security. The condition-key patterns are demonstrated in [`../05-security/01-iam-scoped-access/`](../05-security/01-iam-scoped-access/).
- **Prefer federated, short-lived credentials** for user-facing paths (Cognito → STS `AssumeRoleWithWebIdentity`) over a long-lived shared role. See [`../05-security/02-cognito-federated-identity/`](../05-security/02-cognito-federated-identity/).

### Memory execution role

- Trust policy lets the AgentCore Memory service assume it.
- Permissions: `bedrock:InvokeModel` for the model(s) your strategies use, plus (for self-managed) `s3:PutObject` to the payload bucket and `sns:Publish` to the topic.
- If the resource uses a CMK, this role and the service need `kms:Decrypt`/`kms:GenerateDataKey` on that key.

## 2. KMS encryption

Memory data is encrypted at rest by default with an AWS-owned key. For sensitive or contractually-controlled workloads, supply a **customer-managed key** via `encryptionKeyArn` on `CreateMemory` (see [`../05-security/03-kms-encryption/`](../05-security/03-kms-encryption/)).

- **When to use a CMK:** you need key rotation you control, auditable `kms:Decrypt` in CloudTrail, or the ability to revoke access by disabling the key. One CMK per tenant when a contract demands customer-controlled revocation.
- **Key policy must grant the memory service** (and the execution role) `kms:Decrypt` and `kms:GenerateDataKey` — otherwise creates/reads fail with `AccessDeniedException` and you'll see `StreamUserError` on the streaming path.
- **`encryptionKeyArn` is set at create time.** Decide before you create the resource.
- **Don't put sensitive data in event metadata** regardless of CMK — metadata is more exposed across the API surface than payload content.
- **Disabling the key is a kill switch, not a quiet config change** — every data-plane call against that memory starts failing. That's the intended behavior for revocation; just know it's immediate and total.

## 3. Observability

Wire these up *before* launch — the first time you need them is during an incident. Full metric list and CLI in [`../04-observability/`](../04-observability/).

| Signal | Metric / source | Alarm guidance |
|---|---|---|
| API errors | `Errors` (per data-plane op, namespace `AWS/Bedrock-AgentCore`) | Alarm on a spike — usually a deleted strategy or renamed namespace. |
| Throttling | `Errors` on `CreateEvent`/`RetrieveMemoryRecords` correlated with 429s | Sustained throttling = you've outgrown your quota; request an increase. |
| Stream delivery | `StreamPublishingFailure`, `StreamUserError` | Alarm on **any** `StreamUserError` (page-worthy: broken IAM/KMS); alarm on `StreamPublishingFailure > 0`. |
| Ingestion health | `NumberOfMemoryRecords` per strategy | Alarm on a sudden drop — the canary for an extraction regression. |
| Ingestion errors | Log group `/aws/bedrock-agentcore/memory/<memoryId>` | **Enable log delivery in production** — without it, ingestion failures are invisible. |

- **Pair every alarm with a runbook.** Streaming failures usually want a redrive ([`../02-long-term-memory/08-manage-extraction/`](../02-long-term-memory/08-manage-extraction/)); user errors want an IAM/KMS fix.
- **Audit `bedrock-agentcore:actorId` and `kms:Decrypt` in CloudTrail** — the two signals that tell you who read what.

## 4. Rate limits and quotas

> **These are account- and region-specific, adjustable, and change over time — verify current values; do not hard-code numbers from any tutorial.**

- **CreateEvent is explicitly rate-limited** ("This operation is subject to request rate limiting" — API reference). Expect `ThrottledException` (429) under load and handle it per [`01-error-handling.md`](./01-error-handling.md).
- **Check the Service Quotas console** (search "Bedrock AgentCore") for your per-API, per-account limits, and request increases **before** a launch or load test — quota increases are not instantaneous.

  ```bash
  # Discover the service code, then list default quotas.
  aws service-quotas list-services \
    --query "Services[?contains(ServiceName, 'AgentCore')]"

  aws service-quotas list-aws-default-service-quotas \
    --service-code <service-code-from-above> \
    --query "Quotas[].{Name:QuotaName,Value:Value}" --output table
  ```

**Field constraints that *are* fixed** (from the API references — safe to rely on):

| Constraint | Value | Source |
|---|---|---|
| `eventExpiryDuration` | 3–365 (integer days) | `CreateMemory` |
| `records` per batch call | 0–100 | `BatchCreateMemoryRecords` |
| `payload` items per `CreateEvent` | 0–100 | `CreateEvent` |
| Event `metadata` entries | 0–15 (key 1–128 chars) | `CreateEvent` |
| `RetrieveMemoryRecords` `maxResults` | 1–100 (default 20) | `RetrieveMemoryRecords` |
| `metadataFilters` per retrieve | ≤ 5 (SDK-enforced `ValueError`) | SDK `retrieve_memories` |
| `indexedKeys` per memory | 1–10 items | `CreateMemory` |
| Memory `name` | `[a-zA-Z][a-zA-Z0-9_]{0,47}`, unique per account | `CreateMemory` |
| Tags per memory | 0–50 | `CreateMemory` |

## 5. Multi-region

AgentCore Memory is regional — a memory resource and its data live in one region. Decide your posture explicitly:

- **Single-region (default):** simplest. Pin your runtime and memory to the same region; make the region an explicit config value, not a default buried in code.
- **Active-passive / DR:** there is no built-in cross-region replication of a memory resource. If you need a warm standby, you own the replication — e.g. fan `CreateEvent` writes to a second region, or periodically export records (`list_memory_records`) and `BatchCreateMemoryRecords` into the standby. Budget for the duplicate extraction cost if both regions run strategies.
- **Data residency:** if records must stay in-region for compliance, make `actorId`/namespace and the resource region-aware so a user is always routed to the compliant region.
- **Verify regional availability** of AgentCore Memory before committing an architecture to a given region.

## 6. Conversation persistence (session manager / checkpointer)

If your agent framework holds the conversation, make sure the thing holding it survives a
process restart. This is the most common way a "memory-enabled" agent still forgets.

- **Do not ship an in-process saver.** LangGraph's `InMemorySaver` lives in the Python heap:
  every conversation dies with the process, and two runtime replicas don't share state. Swap
  it for `AgentCoreMemorySaver(memory_id, region_name=region)` before launch. Note that the two
  are not drop-in swaps, since the saver also requires `actor_id` in `configurable`
  (next bullet). See [06-usage-patterns.md](../00-getting-started/06-usage-patterns.md) for usage with frameworks.
- **Pass a stable, non-empty session id on every invocation.** `AgentCoreMemorySaver`
  requires both `thread_id` (→ AgentCore `sessionId`) and `actor_id` (→ `actorId`) in
  `configurable` and raises `InvalidConfigError` without them. On AgentCore Runtime,
  `context.session_id` is optional and can be `None` — default it explicitly rather than
  letting a falsy value reach the checkpointer.
- **Derive `actor_id` from the authenticated principal, not from the request body.** It is
  the value your IAM `bedrock-agentcore:actorId` condition (section 1) is compared against;
  a client-supplied actor id makes that condition decorative.
- **Budget for the write amplification.** A checkpointer writes an event per graph state update, not
  per user turn, so a multi-tool ReAct loop can be several `CreateEvent` calls per turn.
  `CreateEvent` is rate-limited (section 4) and events are billable storage — set
  `eventExpiryDuration` accordingly.
- **Tune the adapter's retries rather than wrapping it.** `AgentCoreMemorySaver` takes
  `max_retries` (default 3), `initial_backoff` (0.1s) and `max_backoff` (2.0s); prefer those
  over a custom retry layer that would double-retry. See [`01-error-handling.md`](./01-error-handling.md).

## 7. Resource cleanup on teardown

Orphaned memory resources keep costing money (event + record storage). Make teardown deliberate.

- **Per-request resources** (tests, ephemeral sessions): wrap create/use/delete in `try/finally` so an exception can't leak the resource. Pattern in [`01-error-handling.md`](./01-error-handling.md#try--finally-cleanup) and [`production-patterns.py`](./production-patterns.py).
- **Per-tenant resources:** on offboarding, `delete_memory` releases events *and* records in one call — cheaper and more complete than record-by-record deletion.
- **Shared resource, per-user cleanup:** if many actors share one memory, delete a churned user's data with `list_memory_records(namespace="/users/{actorId}/")` → `BatchDeleteMemoryRecords`. (See [`02-cost-optimization.md`](./02-cost-optimization.md#3-namespace-and-record-lifecycle).)
- **`delete_memory` takes a `clientToken`** for idempotent retries — pass a fresh UUID, and wrap the delete so a cleanup failure is logged rather than masking the real error.
- **Don't delete long-lived production memories on a per-request path.** Cleanup-on-teardown is for ephemeral resources and offboarding, not steady-state traffic.

---

### Final pre-launch gate

- [ ] Runtime role enumerates only the data-plane actions used, scoped to the memory ARN and `actorId`
- [ ] Control-plane actions kept off the runtime role
- [ ] Memory execution role can `bedrock:InvokeModel` (+ S3/SNS/KMS as needed)
- [ ] CMK configured for sensitive workloads, key policy grants the service `kms:Decrypt`/`kms:GenerateDataKey`
- [ ] Alarms live on `Errors`, throttles, `StreamPublishingFailure`, `StreamUserError`, `NumberOfMemoryRecords`
- [ ] Ingestion log delivery enabled
- [ ] Current quotas checked in Service Quotas; increases requested ahead of launch
- [ ] Region pinned explicitly; multi-region/DR posture decided
- [ ] Conversation persistence uses the durable adapter (`AgentCoreMemorySaver` / `AgentCoreMemorySessionManager`)
- [ ] `actor_id` derived from the authenticated principal; session id always non-empty
- [ ] Teardown path deletes ephemeral/offboarded resources; long-lived ones excluded
