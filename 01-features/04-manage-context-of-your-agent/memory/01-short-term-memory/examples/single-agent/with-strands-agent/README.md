# Short-term memory — Strands single-agent

A personal assistant built with **Strands Agents**, wired to AgentCore short-term memory through lifecycle hooks. The agent loads recent turns on startup (`get_last_k_turns`) and stores each new message as it's added, so a conversation continues seamlessly when the user returns.

| Information | Details |
|---|---|
| Tutorial type | Short-term conversational |
| Agent type | Personal Agent |
| Framework | Strands Agents |
| LLM model | Anthropic Claude Haiku 4.5 |
| Memory components | Short-term memory, `AgentInitializedEvent` + `MessageAddedEvent` hooks, `get_last_k_turns` |
| Complexity | Beginner |

## What it does

- [`personal-agent.py`](./personal-agent.py) — personal assistant with a web-search tool; an `AgentInitializedEvent` hook hydrates history and a `MessageAddedEvent` hook stores each turn via `MemoryClient`.
- [`personal-agent-memory-manager.py`](./personal-agent-memory-manager.py) — the same agent using the newer **`MemoryManager`** / **`MemorySessionManager`** APIs instead of `MemoryClient` (useful as a migration reference).
- [`travel-planning-branching/`](./travel-planning-branching/) — forks conversation history into branches to explore alternative paths.

> ### Three similarly named things — which one is which?
> These examples wire memory with **lifecycle hooks**, so you can see each `create_event` and
> `get_last_k_turns` call. Strands can also do it for you, and the class names are close
> enough to be genuinely confusing:
>
> | Name | What it is | Import from |
> |---|---|---|
> | `MemoryClient` | The AgentCore SDK's low-level client. Used by [`personal-agent.py`](./personal-agent.py). | `bedrock_agentcore.memory` |
> | `MemorySessionManager` | An AgentCore **SDK** helper over `MemoryClient` — framework-agnostic, works with any framework or none. Used by [`personal-agent-memory-manager.py`](./personal-agent-memory-manager.py). | `bedrock_agentcore.memory.session` |
> | `AgentCoreMemorySessionManager` | The **Strands adapter**. Pass it as `Agent(session_manager=...)` and Strands saves and restores the conversation itself — no hooks to write. | `bedrock_agentcore.memory.integrations.strands.session_manager` |
>
> The third one is what you'd typically ship — it's demonstrated in
> [`../../../../03-integrations/01-runtime-integration/`](../../../../03-integrations/01-runtime-integration/),
> and compared against the other frameworks' adapters in
> [06-usage-patterns.md](../../../../00-getting-started/06-usage-patterns.md).

## Prerequisites

- Python 3.10+
- AWS credentials with AgentCore Memory permissions
- Access to Amazon Bedrock models

## How to run

```bash
pip install -r requirements.txt
python personal-agent.py
```

See the [single-agent index](../README.md) and the [short-term memory section README](../../../README.md) for context.
