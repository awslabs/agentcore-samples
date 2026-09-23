# Long-term memory — Claude SDK single-agent

Long-term memory examples built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), with **no agent framework**. The Anthropic SDK is a stateless API client — no built-in conversation management, hooks, or session handling — so every memory operation is explicit. That makes these the clearest illustration of what AgentCore long-term memory actually does for you.

## Short-term vs. long-term memory

| | Short-term memory | Long-term memory |
|---|---|---|
| **Stores** | Raw conversation turns, verbatim | Distilled records: facts, summaries, preferences, episodes |
| **How** | `create_event` → `get_last_k_turns` | `create_event` → asynchronous extraction → `retrieve_memories` |
| **Scope** | Within a session (resume a conversation) | Across sessions (remember the user over time) |
| **Retrieval** | Recency (last *k* turns) | Semantic search over a namespace |
| **Latency** | Immediate | Extraction runs ~30–90s after the event |
| **Where it lives here** | [`../../../01-short-term-memory/examples/single-agent/with-claude-sdk/`](../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/) | This folder |

Both use the **same** `create_event` call. Attaching **strategies** to the memory resource is what turns stored turns into long-term records — no separate extraction API.

## The three integration patterns

Long-term memory can be wired into an agent in three ways. The same patterns appear across every framework (Strands, LangGraph, LlamaIndex); here they're shown on the raw Claude SDK.

| Pattern | What it is | Status |
|---|---|---|
| **[01 — Built-in strategies](./01-built-in-strategies/)** | Create the memory with built-in strategies (Semantic, User Preference, …); store every turn with `create_event`; retrieve distilled records with `retrieve_memories` and inject them into the system prompt. No IAM role required. This is the post-response pattern: store after each reply, retrieve at the start of the next session. | ✅ Available |
| **[02 — Custom hook / strategy override](./02-custom-strategy-override/)** | Override the built-in extraction or consolidation models and prompts, or orchestrate retrieval/storage with custom logic (conditional saves, multi-strategy merges, citations). | ✅ Available |
| **[03 — Memory as a tool](./03-memory-as-tool/)** | Expose memory operations (`store_memory`, `recall_memory`) as tools the LLM decides to call via the `tools=` parameter; run the agentic loop (`tool_use` → `tool_result` → continue) so the agent manages its own memory lifecycle. `store_memory` calls `create_event`; `recall_memory` calls `retrieve_memories`. | ✅ Available |

> **Why "post-response" for pattern 01?** Frameworks like Strands provide an `AgentCoreMemoryHook` that fires on the agent lifecycle (e.g. after invocation) to save the turn and before the next to retrieve. The Claude SDK has no such lifecycle, so we replicate the same behavior explicitly — store with `create_event` right after each assistant reply, and retrieve + inject at the start of the next session.

## Strategy variation: Episodic memory

The patterns above use the **Semantic** strategy (distill isolated *facts*). The Episodic strategy is different — it captures whole multi-turn *episodes* ("what happened last time, and how did it go?") and adds a **Reflection** step that derives cross-episode patterns. It uses the same `create_event` / `retrieve_memories` calls as pattern 01; only the strategy configuration changes.

| Strategy variation | What it is | Status |
|---|---|---|
| **[04 — Episodic memory](./04-episodic-memory/)** | Use the built-in Episodic strategy to capture complete debugging sessions as episodes (situation, intent, actions, outcome) plus cross-episode Reflections. Requires a `reflectionConfiguration` namespace, and each episode must end with a clear conclusion so AgentCore detects episode completion. Extraction is slower than Semantic (episodes surface ~15–20 min after the events). | ✅ Available |

## Models

All examples use **Claude Sonnet 4.6** on Amazon Bedrock — `global.anthropic.claude-sonnet-4-6`. The `global.` prefix selects Bedrock's global (cross-region) inference endpoint (the default for Sonnet 4.6, no regional pricing premium). Swap to `us.anthropic.claude-sonnet-4-6` to pin traffic to US regions.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model-invocation permissions.
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region (request it in the Bedrock console under *Model access*; `us-west-2` is a safe default).

## How to run

Each sub-folder is self-contained:

```bash
cd 01-built-in-strategies
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python claude-sdk-ltm-semantic.py
```

## Where to go next

- Long-term memory overview (strategies, namespaces, retrieval, AWS CLI): [`../../../README.md`](../../../README.md)
- The same patterns in a framework: [`../with-strands-agent/`](../with-strands-agent/), [`../with-langgraph-agent/`](../with-langgraph-agent/), [`../with-llamaindex-agent/`](../with-llamaindex-agent/)
- Short-term memory with the Claude SDK: [`../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/`](../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/)
