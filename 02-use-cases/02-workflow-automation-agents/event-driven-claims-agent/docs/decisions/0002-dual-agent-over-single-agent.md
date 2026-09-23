# ADR-0002: Dual-Agent Over Single-Agent

**Status:** Accepted  
**Date:** 2025-06-17

## Context

Claims processing requires both evaluation (is this claim valid?) and validation (is our evaluation correct?). A single agent could perform both roles, but may exhibit confirmation bias.

## Decision

Use two sequential Strands `Agent` instances (Claims Processor → Validation Agent) rather than a single agent.

## Reasoning

A single agent asked to both evaluate a claim and validate its own evaluation exhibits confirmation bias — it rarely overrides its first decision. The Validation Agent is intentionally isolated from the Processor's reasoning process until it receives the full output, acting as an independent reviewer. This produces more accurate confidence scores and better human-review routing for edge cases (vague claims, high amounts, category mismatches).

## Alternatives Considered

A single agent with a two-phase prompt shows poor self-correction — the agent consistently validates its own decisions even when they are incorrect.

## Consequences

Two LLM calls per claim instead of one. Adds ~10-15 seconds to processing time. Sequential design (not parallel) simplifies the code — no shared state needed between agents.
