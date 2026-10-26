# Cost optimization

AgentCore Memory cost comes from a few distinct levers, and they pull in different directions. This document is about the levers you control — what each one does, and the trade-off you're making when you turn it.

> **On exact prices.** Per-unit pricing (storage, events, extraction) is set on the [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) and changes over time — we don't reproduce dollar figures here. What's durable, and what this doc covers, is *which actions cost money* and *how your configuration multiplies them*. **Bedrock model invocations for built-in strategies bill against your account separately** from any Memory charge — that's the cost that surprises people, and it scales with strategy count and event volume.

## The cost model in one picture

```
                      stored for `eventExpiryDuration` days
  CreateEvent ──► short-term events ──────────────────────────► (storage cost)
                          │
                          │  if strategies are configured, each event triggers...
                          ▼
              ┌──────────────────────────────┐
              │  async extraction pipeline    │   one job PER strategy,
              │  (Bedrock model invocation)   │   each a billed Bedrock call
              └──────────────────────────────┘
                          │
                          ▼
              long-term memory records ───────────────────────► (storage cost,
                                                                  lives with the resource)
```

Two storage buckets, plus a compute (Bedrock) cost that fires on the way from one to the other. Optimize each.

## 1. Event expiry tuning (`eventExpiryDuration`)

Short-term events are retained for `eventExpiryDuration` days, then expire automatically. **Valid range: 3–365 days** (integer; required on `CreateMemory`) — confirmed in the [`CreateMemory` API reference](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateMemory.html). The longer the window, the more raw events you store and pay for.

| Setting | Good fit |
|---|---|
| **Short (3–7 days)** | Real-time assistants where long-term records (not raw events) carry the durable value. The tutorials in this repo use 7–30. Streaming and identity samples use 7. |
| **Medium (30–90 days)** | Default working range; 90 is the SDK's `create_memory` default. Enough history to redrive a failed extraction or debug a session. |
| **Long (up to 365)** | Compliance/audit needs to retain raw conversation, or slow extraction cadences that must reach back weeks. |

Guidance:
- **Set it deliberately.** It's a *required* field — don't copy `30` from a sample without thinking about your retention need.
- **Decouple it from long-term value.** If your durable value lives in extracted *records*, a short event window is fine — records persist with the resource regardless of event expiry.
- **Don't over-retain "just in case."** Raw events are the cheaper-to-regenerate, higher-volume bucket. A 365-day window on a high-traffic agent is a large standing cost for data you rarely read.

## 2. Strategy selection — every strategy is a separate extraction job

This is the biggest hidden multiplier. Each configured strategy runs its **own** extraction (and consolidation, and for episodic, reflection) over your events — and each of those steps is a **Bedrock model invocation billed to your account** (built-in strategies require a `memoryExecutionRoleArn` precisely because the model runs as you).

So: **N strategies ≈ N× the extraction model cost** over the same event stream.

| Lever | Effect |
|---|---|
| **Fewer strategies** | Each one you remove eliminates a full extraction job per qualifying event. Configure only the strategies whose records you actually retrieve. |
| **Right strategy for the need** | `SEMANTIC` (facts), `SUMMARIZATION` (running summary), `USER_PREFERENCE` (preferences), `EPISODIC` (events + cross-episode reflection). Episodic does the most model work — use it only when you need cross-session episode reasoning. |
| **Overrides over redundancy** | If a built-in *almost* fits, override its extraction/consolidation prompt (see [`../02-long-term-memory/02-strategy-overrides/`](../02-long-term-memory/02-strategy-overrides/)) rather than stacking a second strategy. |
| **Self-managed for control** | A self-managed strategy lets you choose the model and trigger cadence — and *batch* extraction so you're not invoking a model per event. Trade-off: you operate it. See [`../02-long-term-memory/03-self-managed-strategy/`](../02-long-term-memory/03-self-managed-strategy/). |

Audit prompt: **for each strategy, name the retrieval that reads its records.** If you can't, that strategy is paying extraction cost for records nobody queries — remove it.

## 3. Namespace and record lifecycle

Long-term records persist for the life of the memory resource — there's no automatic TTL on a record the way there is on an event. Left alone, record storage grows monotonically.

- **Prune stale records.** Use `BatchDeleteMemoryRecords` (by `memoryRecordId`) to remove records that are no longer useful — superseded preferences, expired facts, or per-compliance deletions. See [`../02-long-term-memory/07-skip-STM/`](../02-long-term-memory/07-skip-STM/).
- **List then delete by namespace.** `list_memory_records(namespace=...)` enumerates a namespace; pair it with batch-delete to clean a whole branch (e.g. a churned tenant's `/users/{actorId}/`).
- **Namespaces are organization, not access control.** Design them hierarchically (trailing `/`) so a single prefix scan finds everything you need to clean up. (Security boundary is IAM — see [`../05-security/`](../05-security/).)
- **Delete the resource for whole-tenant offboarding.** If an actor/tenant maps to its own memory resource, deleting that resource releases both events and records in one call. See [`03-production-checklist.md`](./03-production-checklist.md#7-resource-cleanup-on-teardown).

## 4. Batch-API economics

When you write or delete records yourself (self-managed extraction, back-fills, migrations, pruning), the batch APIs are dramatically cheaper per record than one call each:

- **Up to 100 records per call** (documented max for the `records` array). Fewer API calls = less request overhead and less throttling exposure.
- **Chunk large workloads at 100** and parallelize the chunks (mind your throttle ceiling — see [`01-error-handling.md`](./01-error-handling.md)).
- **Inspect `failedRecords`** so you only re-submit the subset that failed, not the whole batch.
- Batch CRUD **bypasses extraction** — no Bedrock model cost. That's the cheap path when *you've already done the extraction*. Don't use `CreateEvent`-with-a-strategy if all you want is to write a record you already have.

## 5. STM-only vs. LTM strategies

The single highest-leverage decision: **do you need extraction at all?**

| Use **STM-only** (no strategies) when… | Reach for **LTM strategies** when… |
|---|---|
| Context only needs to live within a session or a few days | You need recall *across* sessions (preferences, facts, history) |
| You just need the conversation transcript (`ListEvents` / `get_last_k_turns`) | You want *structured insight* (a summary, a preference set) rather than raw turns |
| You're cost- or latency-sensitive and raw history is enough | The value of better personalization clearly exceeds per-event extraction cost |

A memory with **no strategies configured runs no extraction pipeline** — you pay only event storage, and you've eliminated the entire Bedrock-invocation cost line. Many "remember the last few turns" use cases are STM-only and people reach for strategies reflexively. Start STM-only; add a strategy when a concrete retrieval need justifies it.

## Quick wins checklist

- [ ] `eventExpiryDuration` set to your actual retention need, not a copied default
- [ ] Every configured strategy maps to a retrieval that reads its records
- [ ] Episodic used only where cross-episode reasoning is needed
- [ ] STM-only for "recent context" use cases; strategies added only when recall-across-sessions pays off
- [ ] Stale long-term records pruned (no record auto-TTL)
- [ ] Self-managed writes/back-fills go through batch APIs (100/call), not per-record `CreateEvent`
- [ ] Whole-tenant offboarding deletes the resource, not record-by-record
</content>
