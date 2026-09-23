# ADR-0012: Cedar Gates on Tool Input, Not Caller Identity

**Status:** Accepted
**Date:** 2026-06-24

## Context

We want a deterministic guardrail: an expense at or above a threshold ($2,000) must not auto-persist — it goes to human review regardless of what the agents decide. The agents *should* route it correctly, but a guardrail that depends on the agents reasoning correctly isn't a guardrail. AgentCore Policy (Cedar) enforces at the Gateway, before the tool Lambda runs. The question is what the policy keys on.

## Decision

Cedar gates on the **tool input**, not the caller's identity:

```
forbid(principal, action, resource is AgentCore::Gateway)
when {
  context has input
  && !(context.input has reason)
  && context.input has total
  && context.input.total >= 2000
};
```

Paired with a broad `permit(principal, action, resource is AgentCore::Gateway)` (allow-all + selective-deny). Caller identity is agent-as-principal M2M ([ADR-0004](0004-agent-as-principal-m2m-over-per-user-jwt.md)); per-user separation is at the data layer. Cedar's job is purely "may this *call*, with these *arguments*, proceed?"

## Reasoning

The guardrail must be independent of the agents. Gating on the input means the `$2,000` rule holds even if the validator is shed (L2 down) or the extractor misjudges — the Gateway denies the `save_expense` call deterministically, and the agent falls back to `human_review`. Three subtleties, each learned by deploying and reading the Cedar docs (not by guessing):

1. **Guard `context has input` FIRST.** Referencing `context.input` unconditionally fails the policy-engine *create* for actions like `AgentCore::Action::"Http"` that carry no `input` attribute — and `IGNORE_ALL_FINDINGS` does **not** suppress that. Cedar `&&` short-circuits, so the `has input` guard must come first.
2. **Scope to the save path.** A bare `total >= 2000` would block *every* tool carrying a `total`, including `human_review` (the safe fallback). `!(context.input has reason)` scopes it: `save_expense` has no `reason`, `human_review` requires one.
3. **Fail closed on ambiguity.** A fractional total makes the decimal-vs-Long comparison error; the forbid then fails closed and routes that save to review — a safe, conservative "when in doubt, review."

## Alternatives Considered

- **Gate on `context.toolName == "save_expense"`** (the instinct from some examples): does not gate here — the documented Cedar tool-args schema keys on `context.input.<field>`. Reading the Cedar policy-conditions docs first, instead of pattern-matching a different sample, was the lesson.
- **Enforce the threshold in the Lambda or the agent:** not deterministic-independent — a bug or a shed validator could bypass it. The Gateway is the one boundary every tool call must pass.

## Consequences

The `$2,000` rule is enforced at the Gateway, before any write, independent of the agents. A denied `save_expense` surfaces as `cedar_blocked` and the agent routes to `human_review`. The policy uses `IGNORE_ALL_FINDINGS` because it references runtime-resolved input — see [ADR-0013](0013-ignore-all-findings-policy-validation.md). A failed policy *create* leaves a `ROLLBACK_COMPLETE` stack that blocks the next deploy, so a deploy-time policy error needs a manual `delete-stack`.
