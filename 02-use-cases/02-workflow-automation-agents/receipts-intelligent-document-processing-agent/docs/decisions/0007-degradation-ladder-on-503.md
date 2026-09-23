# ADR-0007: A Model Degradation Ladder, Stepped on 503

**Status:** Accepted
**Date:** 2026-06-24

## Context

A GenAI pipeline's hard dependency is model capacity. When Amazon Bedrock returns `503 ServiceUnavailable`, the model is capacity-constrained — and per the Bedrock error reference, `503` is explicitly **not** an account quota problem (that's `429`). AWS's first remedy for `503` is cross-region inference, which a global inference profile already does. So a `503` that survives the global profile means the model is constrained across regions — exactly when switching to a *different* model is the right move. This sample's distinct contribution over the claims sample is to make that graceful degradation a first-class, configurable mechanism.

## Decision

Define a five-rung **degradation ladder**, availability-first, every rung a `global.` inference profile so each step keeps cross-region routing:

| Rung | Model | Behavior |
|------|-------|----------|
| **L0 — Full** | `global.anthropic.claude-opus-4-8` | Full pipeline: extractor + independent validator, memory R/W, merchant lookup, dedup. Default. |
| **L1 — Reduced** | `global.anthropic.claude-opus-4-7` | Drop memory writes + merchant normalization; keep the validator. |
| **L2 — Lean** | `global.anthropic.claude-opus-4-6-v1` | Extractor + persist only; no validator; everything routes to review. |
| **L3 — Fallback** | `global.anthropic.claude-sonnet-4-6` | Same as L2 on an independently-provisioned, cheaper model. |
| **L4 — Defer** | none | No model call: accept the receipt, queue to SQS, return `deferred`. |

The trigger is **`503` only**. `429 ThrottlingException` (quota) and `500 InternalServerException` (transient) back off and retry the **same** model — stepping models doesn't fix a quota problem. The ladder is **data, not code** (it lives in AppConfig — see [ADR-0008](0008-appconfig-over-hand-rolled-flags.md)); repointing or adding a rung is a config edit.

## Reasoning

Availability beats both cost and peak quality when the alternative is failing the request. The top three rungs are all Opus (same tier, same price) stepped by version so each has separate capacity; the floor drops to Sonnet (independently provisioned). Every degraded run is **marked, not silent**: the row and the trace span carry the rung, and from L2 down everything is `needs_review` — no quietly-low-quality auto-writes.

## Alternatives Considered

- **Fail the request on `503`:** the receipt is lost or bounced; unacceptable when a different model has capacity.
- **One model, retry forever:** a `503` is cross-region capacity exhaustion — retrying the same profile just burns time.
- **Cost-first ladder (cheap model first):** wrong objective. The point is to preserve availability and quality as long as possible, degrading only as far as capacity forces.

## Consequences

The system has graceful degradation instead of a hard failure, but at the cost of real machinery: the rung config + reader ([ADR-0008](0008-appconfig-over-hand-rolled-flags.md), [ADR-0009](0009-appconfigdata-not-lambda-extension.md)), two ways to set the rung ([ADR-0010](0010-two-rung-setting-paths.md)), and the L4 buffer + drain ([ADR-0011](0011-l4-sqs-jittered-drain.md)). The exact `global.` profile ids are read from `aws bedrock list-inference-profiles` in the target account, never pattern-constructed — the suffix convention is not uniform (`opus-4-6` is `...-opus-4-6-v1`, with a `-v1`).

> **Incremental adoption.** A reader who only wants the swappable model can take just the config-driven model id + the in-agent step-down and skip the account-level control loop. The full ladder ships here.
