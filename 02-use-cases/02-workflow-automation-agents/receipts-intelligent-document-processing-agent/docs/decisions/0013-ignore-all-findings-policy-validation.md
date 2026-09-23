# ADR-0013: IGNORE_ALL_FINDINGS Policy Validation Mode

**Status:** Accepted
**Date:** 2026-06-24

## Context

The AgentCore Policy Engine lets you pick a Cedar validation mode per policy, matching the level of static checking to where you are in the lifecycle. This sample uses the "allow-all + selective-deny" pattern, where the policies intentionally reference runtime values (the Gateway resource, the tool input) resolved at invocation time. (Carries the claims sample's ADR-0009.)

## Decision

Both Cedar policies use `validationMode: "IGNORE_ALL_FINDINGS"`.

## Reasoning

This mode fits runtime-resolved policies:

- `AllowAllTools` (`permit(principal, action, resource is AgentCore::Gateway)`) is intentionally broad — the classic starting point for "allow-all, then add targeted denies."
- `BlockExcessiveExpense` reads `context.input.total`, a value the agent supplies at invocation time ([ADR-0012](0012-cedar-on-tool-input.md)).

`IGNORE_ALL_FINDINGS` lets these intentional patterns deploy cleanly while the Policy Engine still enforces both at runtime exactly as written, and still catches genuine syntax errors at deploy. (It does **not**, however, suppress the "attribute `input` not found" *create* error for input-less actions — that's why the policy guards `context has input` first; see [ADR-0012](0012-cedar-on-tool-input.md).)

## Alternatives Considered

- **`STRICT` validation:** the right choice for production, paired with a defined entity schema — full static analysis of runtime-resolved references. More than this introductory sample needs.
- **Enumerating every tool in the permit policy:** works, but allow-all + selective-deny is the more common, maintainable starting point worth showing.

## Consequences

The sample deploys with the broad allow-all in place. As you tighten policies, move to `STRICT` with an entity schema for full static guarantees — a one-line change per policy's `validationMode`.
