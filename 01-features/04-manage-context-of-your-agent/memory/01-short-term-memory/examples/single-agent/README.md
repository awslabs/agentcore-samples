# Short-term memory — single-agent examples

One agent per example, each wiring AgentCore short-term memory into a different framework. The same pattern recurs: retrieve recent turns on startup, store each turn as it happens.

| Framework | Folder | Pattern | What it demonstrates |
|---|---|---|---|
| Anthropic Claude SDK (no framework) | [`with-claude-sdk/`](./with-claude-sdk/) | Direct API calls | Explicit `messages[]` management — the clearest view of what short-term memory does |
| Strands Agents | [`with-strands-agent/`](./with-strands-agent/) | Custom hook | Personal agent via lifecycle hooks; `travel-planning-branching/` forks the conversation |
| LangGraph | [`with-langgraph-agent/`](./with-langgraph-agent/) | Built-in hook, custom hook, memory-as-tool | Built-in checkpointing, custom hooks, and human-in-the-loop |
| LlamaIndex | [`with-llamaindex-agent/`](./with-llamaindex-agent/) | Built-in hook | `AgentCoreMemory` context in a `FunctionAgent` across four domains |

Each framework has a class that persists the conversation for you, so you don't call
`create_event` / `list_events` by hand — LangGraph and LlamaIndex use theirs below. The Strands
examples here use **lifecycle hooks** instead, to show what the adapter does under the hood;
for the Strands adapter itself see
[`../../../03-integrations/01-runtime-integration/`](../../../03-integrations/01-runtime-integration/).

Class names and per-framework snippets:
[06-usage-patterns.md](../../../00-getting-started/06-usage-patterns.md).

## Where to go next

- Multi-agent short-term examples: [`../multi-agent/`](../multi-agent/)
- Short-term memory primitives: [`../../README.md`](../../README.md)
