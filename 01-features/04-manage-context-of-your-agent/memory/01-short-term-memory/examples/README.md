# Short-term memory — framework examples

End-to-end agent examples that wire AgentCore **short-term memory** (raw events, `get_last_k_turns`) into real agent frameworks. The [section README](../README.md) covers the underlying primitives — events, sessions, actor/session isolation, branching; these folders show them inside working agents.

| Folder | What's inside |
|---|---|
| [`single-agent/`](./single-agent/) | One agent per example — Claude SDK, Strands, LangGraph, LlamaIndex — using the framework's session manager / checkpointer adapter, built-in/custom hooks, or branching |
| [`multi-agent/`](./multi-agent/) | Multiple agents sharing one memory resource, including branch-per-subagent parallel execution |

## Where to go next

- Short-term memory primitives: [`../README.md`](../README.md)
- Long-term memory examples: [`../../02-long-term-memory/examples/`](../../02-long-term-memory/examples/)
