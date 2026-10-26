# AgentCore Tool Search Plugin — Benchmarks

## Overview

Two benchmarks evaluate the AgentCoreToolSearchPlugin from different angles:

1. **Scaling Benchmark** — Does semantic search reduce token costs as tool count grows?
2. **Tool Search Relevance Benchmark** — Does deriving intent from conversation history help the plugin return more relevant tools?

---

## 1. Scaling Benchmark

**Script**: `tool_search_scaling_benchmark.py`

**Question**: How does token usage and latency compare between loading all tools vs. using semantic search, as tool count increases?

### Approaches

| Approach | Description |
|----------|-------------|
| All Tools | Load every tool via `list_tools` and pass all to the LLM |
| Semantic Search (LLM) | Plugin with `StrandsIntentProvider` — derives intent via LLM, searches gateway |
| Semantic Search (Regex) | Plugin with `RegexIntentProvider` — extracts keywords, no LLM for intent |

### Results

| Tools | All Tools (tokens) | Semantic Search (tokens) | Token Reduction |
|-------|-------------------|--------------------------|-----------------|
| 50 | 3,967 | 790 | 80% |
| 100 | 7,970 | 790 | 90% |
| 200 | 16,025 | 800 | 95% |

Semantic search keeps input tokens constant (~800) regardless of total tool count. The all-tools approach scales linearly.

### Latency (50 tools)

| Approach | Latency |
|----------|---------|
| All Tools | 6.13s |
| Regex Search | 8.04s |
| Semantic Search (LLM) | 9.19s |

The search approaches add ~2-3s overhead (intent derivation + gateway search), but this stays constant while all-tools latency grows with tool count.

### Running

```bash
python benchmarks/tool_search_scaling_benchmark.py \
  --profile genai-demo-admin --region us-east-1 --tool-counts 50 100 200
```

---

## 2. Tool Search Relevance Benchmark

**Script**: `intent_relevance_benchmark.py`

**Question**: Does deriving intent from conversation history help the plugin return more relevant tools from the gateway's semantic search?

### Approaches

| Approach | Description |
|----------|-------------|
| Direct Query | Pass the last user message directly as the search query |
| LLM Intent | `StrandsIntentProvider` derives intent from full conversation history |
| Regex Intent | Extract keywords from the last user message (no LLM) |

### Test Categories

- **Single-turn** (3 cases): Direct single-message queries — "What is the weather in Seattle?"
- **Multi-turn** (4 cases): Ambiguous latest message that requires conversation context — "What should I pack?" (after discussing Tokyo trip)
- **Topic shift** (3 cases): Conversation changes direction — "Wait, we had a security incident. Can you scan our systems?" (mid-conversation about analytics)

### Metrics

- **Hit Rate**: Did at least one tool from the expected category appear in the search results?
- **Precision**: Of the 10 tools returned by the gateway, what percentage matched the expected category?

### Results

| Approach | Hit Rate | Avg Precision |
|----------|----------|---------------|
| LLM Intent | 90% (9/10) | 78% |
| Direct Query | 80% (8/10) | 55% |
| Regex Intent | 80% (8/10) | 57% |

### By Test Category

| Category | LLM Intent | Direct Query | Regex Intent |
|----------|------------|-------------|--------------|
| Single-turn | 3/3 hits, 100% precision | 3/3 hits, 87% precision | 3/3 hits, 80% precision |
| Multi-turn | 4/4 hits, 90% precision | 3/4 hits, 45% precision | 2/4 hits, 43% precision |
| Topic shift | 2/3 hits, 40% precision | 2/3 hits, 37% precision | 3/3 hits, 53% precision |

### Multi-turn Test Cases

These show why conversational context matters — the final message alone is meaningless without history:

**Trip planning → weather**
> User: "I'm planning a trip to Tokyo next week"
>
> Assistant: "That sounds exciting! How can I help you prepare?"
>
> User: "What should I pack?"

Expected tools: weather | LLM derived: "packing advice for trip to Tokyo" ✓ | Raw/Regex: missed ✗

**Project discussion → email**
> User: "The quarterly review meeting went well yesterday"
>
> Assistant: "Great to hear! What were the key outcomes?"
>
> User: "Can you let the team know about the new deadlines?"

Expected tools: email/notification | All approaches: hit ✓

**Server issues → monitoring**
> User: "Users are reporting the app is slow today"
>
> Assistant: "I can help investigate. What would you like me to check?"
>
> User: "Check if something is wrong"

Expected tools: monitoring | LLM: 70% precision ✓ | Raw: 60% ✓ | Regex: 70% ✓

**Database migration → database**
> User: "We need to migrate the user data to the new schema"
>
> Assistant: "I can help with that. What's the first step?"
>
> User: "First, show me what we have currently"

Expected tools: database | LLM: 90% precision ✓ | Raw: 20% ✓ | Regex: "currently" → missed ✗

### Running

```bash
python benchmarks/intent_relevance_benchmark.py \
  --profile genai-demo-admin --region us-east-1 --tool-count 100
```

---

## Summary

| Dimension | Finding |
|-----------|---------|
| Token efficiency | Semantic search saves 80-95% of input tokens at 50-200 tools |
| Latency tradeoff | ~2-3s overhead per turn, stays constant as tool count grows |
| Search quality (single-turn queries) | All approaches work equally well |
| Search quality (multi-turn) | LLM intent is 2x better precision than raw/regex |
| Regex vs LLM | Regex saves ~1.2s but fails on ambiguous/contextual messages |

### When to use the plugin

- Your gateway has **10+ tools** across multiple targets
- Your agent handles **multi-turn conversations** where users reference earlier context
- You want to **reduce token costs** without sacrificing tool relevance
- You need **dynamic tool availability** that adapts to conversation context

For agents with a small, fixed set of tools (< 10) or strictly single-turn interactions, the plugin's overhead may not be justified — just load all tools directly.

---

## Prerequisites

- AWS credentials with AgentCore Gateway access
- `mcp-proxy-for-aws` installed (`pip install mcp-proxy-for-aws`)
- Infrastructure is auto-provisioned and torn down by each benchmark script

## Output Files

- `results/benchmark_results.json` — scaling benchmark results
- `results/intent_relevance_results.json` — tool search relevance results (detailed per-test JSON)
