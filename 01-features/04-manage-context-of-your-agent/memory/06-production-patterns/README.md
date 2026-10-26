# Production patterns

Most tutorials in this tree show the **happy path** — create a memory, write an event, retrieve a record, and assume every call succeeds. That's the right shape for learning a primitive, but it's not what you ship. In production the service throttles you, a strategy gets deleted out from under a running agent, a KMS key is disabled, a transient `ServiceException` lands in the middle of a conversation, and a teardown that doesn't clean up leaks resources and cost.

This section collects the patterns that turn the happy-path samples into something you can run in front of users.

## What's inside

| Document | Covers |
|---|---|
| [`01-error-handling.md`](./01-error-handling.md) | The exceptions AgentCore Memory raises, which are retry-able, exponential backoff with jitter, graceful degradation, and `try/finally` cleanup |
| [`02-cost-optimization.md`](./02-cost-optimization.md) | Event-expiry tuning, the per-strategy cost of extraction, namespace/record lifecycle, batch-API economics, and STM-only vs. LTM |
| [`03-production-checklist.md`](./03-production-checklist.md) | Least-privilege IAM, KMS encryption, observability, quotas, multi-region, durable conversation persistence, and resource cleanup |
| [`production-patterns.py`](./production-patterns.py) | A reference implementation of every pattern — copy-paste, not a runnable demo |

## Production readiness checklist

Use this as the index; each row links to the section that explains the *why* and shows the *how*.

| Area | You've done it when… | Where |
|---|---|---|
| **Error handling** | Every data-plane call is wrapped; retry-able vs. terminal errors are distinguished, not blanket-retried | [`01`](./01-error-handling.md) |
| **Retry/backoff** | Retries use exponential backoff **with jitter** and a capped attempt count | [`01`](./01-error-handling.md) |
| **Graceful degradation** | The agent still answers if memory is unavailable — memory is an enhancement, not a hard dependency on the response path | [`01`](./01-error-handling.md) |
| **Partial-failure handling** | Batch calls inspect `failedRecords`, not just the HTTP status | [`01`](./01-error-handling.md) |
| **Event expiry** | `eventExpiryDuration` is set deliberately (3–365 days) to bound storage, not left at a default | [`02`](./02-cost-optimization.md) |
| **Strategy count** | Each configured strategy is justified — every strategy is a separate extraction job that bills Bedrock | [`02`](./02-cost-optimization.md) |
| **STM vs. LTM** | You use STM-only where you don't need extracted insight, and reach for strategies only where recall across sessions pays for itself | [`02`](./02-cost-optimization.md) |
| **IAM least-privilege** | The runtime role grants only the data-plane actions it uses, scoped by `actorId`/namespace conditions | [`03`](./03-production-checklist.md) |
| **KMS** | Sensitive workloads use a customer-managed key via `encryptionKeyArn`, with the key policy granting the memory service | [`03`](./03-production-checklist.md) |
| **Observability** | CloudWatch alarms on `Errors`, throttles, and `StreamPublishingFailure`; ingestion log delivery enabled | [`03`](./03-production-checklist.md) and [`../04-observability/`](../04-observability/) |
| **Quotas** | You've checked the current per-account/per-API quotas in Service Quotas and requested increases before launch | [`03`](./03-production-checklist.md) |
| **Multi-region** | You've decided whether memory is single-region or replicated, and made the actor/namespace scheme region-aware if replicated | [`03`](./03-production-checklist.md) |
| **Conversation persistence** | The framework's session manager / checkpointer is the durable AgentCore adapter, not an in-process saver, and every invocation carries a non-empty actor id and session id | [`03`](./03-production-checklist.md#6-conversation-persistence-session-manager--checkpointer) |
| **Cleanup** | Agent/tenant teardown deletes the memory resource (or its records) so you don't pay for orphaned data | [`03`](./03-production-checklist.md) |

## A note on what's verified here

Everything in this section is grounded in one of three sources, and we say which:

- **API Reference** — the per-operation error lists and field constraints (e.g. `eventExpiryDuration` range 3–365) come from the [AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/) and [AgentCore Control](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/) API references.
- **SDK source** — retry/limit behaviour of the `bedrock_agentcore` Python SDK (`MemoryClient`) is read from the installed package.
- **AWS docs** — conceptual guidance from the [AgentCore Memory dev guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html).

**Per-API request-rate quotas (TPS) are account- and region-specific and are not reproduced here** — they change and are adjustable. Check the **Service Quotas console** (search "Bedrock AgentCore") before capacity planning. See [`03-production-checklist.md`](./03-production-checklist.md#4-rate-limits-and-quotas).

## See also

- [`../04-observability/`](../04-observability/) — the metrics and alarms this section's checklist refers to
- [`../05-security/`](../05-security/) — the IAM/Cognito/KMS deep-dives this section summarizes
- [`../02-long-term-memory/07-skip-STM/`](../02-long-term-memory/07-skip-STM/) — partial-failure handling on batch calls
- [`../02-long-term-memory/08-redrive/`](../02-long-term-memory/08-redrive/) — recovering failed async extractions
</content>
</invoke>
