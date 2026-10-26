# ADR-0002: Dual-Agent Over Single-Agent

**Status:** Accepted
**Date:** 2026-06-24

## Context

Receipt processing needs both extraction (read the fields off the OCR) and validation (is the extraction correct, and should it auto-persist or go to human review?). A single agent could do both, but may exhibit confirmation bias.

## Decision

Use two sequential Strands `Agent` instances — an **extractor** then an independent **validator** — rather than one agent that checks its own work. (Carries the claims sample's ADR-0002 conclusion; the receipts pipeline has the same shape.)

## Reasoning

A single agent asked to both extract and validate its own extraction rarely overrides its first answer. The validator is deliberately isolated from the extractor's reasoning: it sees only the original OCR plus the extractor's structured output, and it owns the auto-persist-vs-`human_review` decision. That produces honester confidence and better routing on the hard cases — totals that don't reconcile, a vague merchant, a large amount on weak evidence.

The extractor is forced to emit a structured expense via a tool call (`submit_expense`), so its output is machine-checkable rather than free text. A deterministic line-item table parser runs first and hands the agent pre-parsed rows; the agent only re-derives rows the parser couldn't (the hybrid approach — see [tutorial.md](../tutorial.md)).

## Alternatives Considered

A single agent with a two-phase prompt: simpler and one fewer LLM call, but the same self-validation weakness the claims sample measured. Rejected for the same reason.

## Consequences

Two LLM calls per receipt instead of one (~10-15s more). The validator is the first **sheddable feature** on the degradation ladder — it runs at L0/L1 and is dropped from L2 down, where every receipt routes to review anyway (see [ADR-0007](0007-degradation-ladder-on-503.md)). Sequential (not parallel) keeps the code simple: no shared state between the two agents.
