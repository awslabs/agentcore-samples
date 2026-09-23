# Long-term memory — single-agent examples

One agent per example, each giving a single agent long-term memory backed by AgentCore. The folders differ by framework; within each, the same three integration patterns recur (built-in, custom, memory-as-tool).

| Framework | Folder | What it demonstrates |
|---|---|---|
| Anthropic Claude SDK (no framework) | [`with-claude-sdk/`](./with-claude-sdk/) | Built-in strategies, custom override, memory-as-tool, and episodic memory — all explicit |
| Strands Agents | [`with-strands-agent/`](./with-strands-agent/) | Built-in hook, custom hook (incl. self-managed strategy), and memory-tool patterns |
| LangGraph | [`with-langgraph-agent/`](./with-langgraph-agent/) | Built-in callback, custom callbacks (user-preference, episodic), and memory-as-tool |
| LlamaIndex | [`with-llamaindex-agent/`](./with-llamaindex-agent/) | Built-in memory block, custom memory block, and memory tool |

## Where to go next

- Multi-agent long-term examples: [`../multi-agent/`](../multi-agent/)
- Long-term memory overview: [`../../README.md`](../../README.md)
