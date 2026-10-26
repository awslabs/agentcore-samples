# ADR-0008: SEMANTIC + SUMMARIZATION Memory

**Status:** Accepted  
**Date:** 2025-06-17

## Context

AgentCore Memory supports multiple strategies: SEMANTIC retrieval, SUMMARIZATION, or both.

## Decision

Enable both `SEMANTIC` and `SUMMARIZATION` built-in memory strategies.

## Reasoning

`SEMANTIC` retrieval allows the agent to recall facts about repeat claimants (prior claims, policy history patterns) across sessions. `SUMMARIZATION` compresses session history so the context window doesn't overflow for multi-turn interactions. Together they provide the cross-invocation recall needed for realistic claims processing.

## Alternatives Considered

Using only SEMANTIC or only SUMMARIZATION would limit either cross-session recall or long-conversation handling.

## Consequences

Memory adds latency (retrieval on each invocation). The 90-day expiration prevents unbounded growth. Memory is gracefully bypassed if not deployed (wrapped in try/except), so local dev works without memory.
