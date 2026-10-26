# ADR-0011: L4 SQS Buffer with a Jittered Drain

**Status:** Accepted
**Date:** 2026-06-24

## Context

When the ladder bottoms out at **L4** ([ADR-0007](0007-degradation-ladder-on-503.md)) — every model rung exhausted — there is no model to call. The receipt still has to go somewhere. And when the tier recovers, the backlog of deferred receipts has to be processed without immediately re-breaking the thing that just healed.

## Decision

At L4 the agent accepts the receipt and **queues it to SQS** (never drops it), returning `deferred`. A **drain consumer** Lambda reads that queue at a **bounded, jittered rate** and re-invokes the Runtime; if the ladder is still at L4, the agent simply re-defers (the message recirculates).

The drain rate is bounded structurally: **reserved concurrency 1** + **SQS batch size 1** + a **jittered pre-invoke sleep**. The queue's **visibility timeout (6 min) exceeds the drain Lambda's timeout (4 min)** so an in-flight replay (a full pipeline run) holds its message intact.

## Reasoning

Two failure modes, two mechanisms. The first is *don't lose the receipt*: SQS buffers it durably for up to 14 days. The second is the real trap — *don't stampede the recovered tier*. A naive "process the whole backlog now" the moment capacity returns would slam the just-recovered model and re-trigger the very `503` that filled the queue (a thundering herd), and the outage never ends. Draining one-at-a-time with jitter makes recovery gradual by construction: the backlog bleeds out at a rate the recovered tier can absorb, and anything still failing recirculates rather than piling on.

This is the accelerator's proven pattern (queue + bounded replay + extended visibility timeout), applied to the ladder's floor.

## Alternatives Considered

- **Drop the receipt at L4:** unacceptable — data loss during an outage is the worst outcome.
- **Drain the whole backlog on recovery:** the thundering herd; re-triggers the `503`.
- **A token-bucket rate limiter in code:** more complex than letting SQS + reserved-concurrency-1 + jitter do it; the queue already gives durability and redelivery for free.

## Consequences

L4 is degrade-*safe*, not degrade-*lossy*. Recovery is paced, not instantaneous — a large backlog takes a while to clear, which is the point. The three resilience layers stay distinct: per-call backoff handles transient `429`/`500`; the SQS buffer handles *volume* at the floor; the ladder handles *capacity* (`503`). Keeping them separate is what makes each one simple.
