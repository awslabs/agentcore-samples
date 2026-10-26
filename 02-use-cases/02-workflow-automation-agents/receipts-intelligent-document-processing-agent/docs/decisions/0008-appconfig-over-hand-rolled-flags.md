# ADR-0008: AWS AppConfig Over a Hand-Rolled Flag Store

**Status:** Accepted
**Date:** 2026-06-24

## Context

The degradation ladder ([ADR-0007](0007-degradation-ladder-on-503.md)) needs to change the system's behavior — which rung is active, which features each rung runs — **without a redeploy**, and to do it safely. Where does that config live?

## Decision

Store the ladder config in **AWS AppConfig** as a freeform JSON profile: `activeRung` plus the per-rung definitions (model id + feature flags). The agent reads it at the start of every run; changing the active rung is a control-plane operation, no stack redeploy.

## Reasoning

A degradation ladder is exactly the use case AppConfig is built for. It gives, out of the box, what a hand-rolled flag table (a DynamoDB item, an S3 object) would force us to reinvent:
- A **validation gate** before a config goes live (catch a malformed rung or unknown model id at deploy, not at 3am).
- **Gradual deployment strategies** (bake time + rollout percentage).
- **Alarm-backed automatic rollback** — wire a CloudWatch alarm to the deployment, and a bad config self-reverts during the bake window.

That safety net is the whole reason to pick AppConfig over a DIY store. The agent caches the config in-process (TTL from the server), so reading the rung is a local lookup, not a per-receipt network call. If AppConfig is ever unreachable or malformed, the reader falls back to L0 and never hard-fails.

## Alternatives Considered

- **A DynamoDB item or S3 object as a flag store:** workable, but we'd hand-roll validation, staged rollout, and rollback — the exact safety features AppConfig provides.
- **Environment variables / redeploy to change the rung:** defeats the purpose. A degradation ladder must change behavior *during* an incident, faster than a deploy.

## Consequences

AppConfig is new infrastructure with **no precedent in the claims sample**, so it was the area to research most carefully. Two findings shaped the implementation: the Runtime reads config via the `appconfigdata` data API, not the Lambda extension ([ADR-0009](0009-appconfigdata-not-lambda-extension.md)); and the account-level controller that *writes* the rung needs IAM on both the AppConfig application and the deployment-strategy resource ([ADR-0010](0010-two-rung-setting-paths.md)). The sample uses an all-at-once, no-bake strategy for fast demos; a production deploy adds a bake window + the alarm rollback.
