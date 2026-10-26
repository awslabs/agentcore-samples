# ADR-0009: Read AppConfig via appconfigdata, Not the Lambda Extension

**Status:** Accepted
**Date:** 2026-06-24

## Context

The agent reads the ladder config from AppConfig ([ADR-0008](0008-appconfig-over-hand-rolled-flags.md)) at the start of each run. The well-known way to read AppConfig with low latency is the **AppConfig Agent Lambda extension** — a layer that serves cached config on `localhost:2772`. The spec originally assumed that path.

## Decision

The agent reads AppConfig via the **`appconfigdata` data API** directly (`StartConfigurationSession` → `GetLatestConfiguration`, polling with the returned token and honoring `NextPollIntervalInSeconds` as a cache TTL), **not** the Lambda extension.

## Reasoning

The AppConfig Lambda extension is a **Lambda layer** — it only exists in the Lambda execution environment. This agent runs in an AgentCore **Runtime container**, not a Lambda, so the extension's `localhost:2772` endpoint simply isn't there. The container-native way to read AppConfig is the `appconfigdata` data API, which is exactly what the extension wraps under the hood. We call it directly, cache the result in-process keyed on the server-provided poll interval, and fall back to L0 on any error so the agent never hard-fails because it couldn't read the ladder.

This is the single load-bearing correction from grounding the AppConfig work first-hand: **container ≠ Lambda**, so the extension does not apply.

## Alternatives Considered

- **The Lambda AppConfig extension:** not available in a Runtime container; it's a Lambda-only layer. The assumption it would work was the thing grounding caught.
- **Re-fetch on every invocation (no cache):** correct but wasteful — a network round-trip per receipt. The data API already hands back a poll interval; honor it.

## Consequences

`model/ladder.py` owns a small `appconfigdata` client + an in-process TTL cache and a pure `resolve_rung()`. The IAM grant for the *reader* (the Runtime role) is `appconfig:StartConfigurationSession` + `appconfig:GetLatestConfiguration`. Note this differs from the *writer* (the controller) grants — see [ADR-0010](0010-two-rung-setting-paths.md).
