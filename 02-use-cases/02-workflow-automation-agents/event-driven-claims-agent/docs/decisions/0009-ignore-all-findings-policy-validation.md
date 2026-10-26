# ADR-0009: IGNORE_ALL_FINDINGS Policy Validation Mode

**Status:** Accepted  
**Date:** 2025-06-17

## Context

The AgentCore Policy Engine lets you pick a Cedar validation mode per policy, so you can match the level of static checking to where you are in the lifecycle. This sample uses the common "allow-all + selective-deny" pattern, where policies intentionally reference runtime values (Gateway ARNs, tool input) that are resolved at invocation time.

## Decision

Both Cedar policies use `validationMode: "IGNORE_ALL_FINDINGS"`.

## Reasoning

This mode is the right fit for a sample built around runtime-resolved policies:

- The `AllowAllTools` policy (`permit(principal, action, resource is AgentCore::Gateway)`) is intentionally broad — it lets any authenticated agent call any tool on this gateway, the classic starting point for the "allow-all, then add targeted denies" pattern.
- The `BlockExcessiveClaims` policy reads `context.input.estimated_amount`, a value the agent supplies at invocation time.

`IGNORE_ALL_FINDINGS` lets these intentional patterns deploy cleanly while the Policy Engine enforces both policies at runtime exactly as written. AgentCore still validates policy syntax in this mode, so genuine typos are caught at deploy.

## Alternatives Considered

- **`STRICT` validation:** The right choice for production, paired with a well-defined entity schema. It applies full static analysis to runtime-resolved references, which is more than this introductory sample needs.
- **Enumerating every tool in the permit policy:** Works, but the allow-all + selective-deny pattern is the more common and maintainable starting point worth demonstrating.

## Consequences

The sample deploys with the broad allow-all pattern in place. As you add or tighten policies, move to `STRICT` mode with an entity schema for full static guarantees — a one-line change to each policy's `validationMode`.
