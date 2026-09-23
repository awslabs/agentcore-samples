# Long-term memory — LangGraph, memory as a tool

A LangGraph agent where AgentCore **long-term memory** is exposed as **tools the model
decides to call**. We define `store_memory` and `recall_memory` as LangGraph `@tool`s and
pass them to `create_agent`; the agent runs the standard **ReAct loop** (model ⇄
`ToolNode`) and chooses, on its own, when to persist a durable fact and when to search its
past knowledge.

The two callback tutorials wired memory *around* the model.
[`01-built-in-callback/`](../01-built-in-callback/) persisted and recalled on every turn
via middleware; [`02-custom-callback/`](../02-custom-callback/) did the same with
hand-rolled hooks. In both, **your code** decided when to save and recall. Here we hand
that decision to the agent: the model emits a tool call, LangGraph executes it, and
AgentCore stores (`create_event`) or retrieves (`retrieve_memories`).

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent usecase | Personal assistant |
| Framework | LangGraph (v1.0 `create_agent`) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in** (no IAM execution role) |
| Memory components | `create_memory_and_wait` (Semantic strategy), `create_event`, `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Tool components | LangGraph `@tool`, ToolNode ReAct loop via `create_agent` |
| Complexity | Advanced |

> ### ⚠️ LangGraph v1.0 API note
> This tutorial uses the current **`from langchain.agents import create_agent`** API. The
> older `langgraph.prebuilt.create_react_agent` is **deprecated** in LangGraph v1.0 — it
> still runs but emits a deprecation warning pointing here. `create_agent` builds the same
> tool-calling ReAct loop and accepts a `tools=` list directly, so the memory-as-tool
> pattern carries over unchanged; only the import and the `prompt=`→`system_prompt=`
> rename differ.

## What it does

[`langgraph-ltm-memory-tool.py`](./langgraph-ltm-memory-tool.py):

1. Creates (or reuses) a memory resource with a built-in **Semantic** strategy. No IAM
   execution role required.
2. Declares two tools — `store_memory` and `recall_memory` — bound to the memory resource
   and session, and passes them to `create_agent`.
3. Runs **Session 1**: a multi-turn conversation where the user shares durable facts
   (name, dietary restrictions, a training goal). The agent calls `store_memory` on its
   own as those facts come up — backed by `create_event`.
4. Polls until the asynchronous extraction surfaces records (~30–90s).
5. Runs **Session 2**: a brand-new conversation with a **fresh message list** — no
   in-session history. The user asks something that depends on the past, and the agent
   calls `recall_memory` itself (backed by `retrieve_memories`) to recover the context
   before answering.
6. Deletes the memory resource in a `finally` block.

Crucially, the demo never injects memory into the prompt and never calls `create_event` /
`retrieve_memories` directly in the conversation flow — **every memory operation happens
because the model asked for it.**

## Architecture

```
  ┌───────────────────────── create_agent (LangGraph ReAct loop) ─────────────────────────┐
  │                                                                                        │
  │     model (Bedrock, Claude Sonnet 4.6)  ──tool call──▶  ToolNode                        │
  │            ▲                                              │                             │
  │            │  ToolMessage (result)                        │  store_memory / recall_memory│
  │            └──────────────────────────────────────────────┘                             │
  └────────────────────────────────────────────────────────────┼──────────────────────────┘
                                                                 │
             store_memory → create_event                        │
             recall_memory → retrieve_memories                  ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │  AgentCore Memory                                                        │
  │   create_event ──▶ short-term events ──▶ async Semantic extraction ──▶   │
  │   long-term records ◀── retrieve_memories (semantic search by namespace) │
  └────────────────────────────────────────────────────────────────────────┘
```

## The tools

| Tool | Model calls it when… | Backed by |
|---|---|---|
| **`store_memory`** | the user shares a durable fact worth remembering across sessions (name, preferences, goals, constraints) | `create_event(memory_id, actor_id, session_id, messages=[(fact, "USER")])` — feeds the Semantic extraction pipeline |
| **`recall_memory`** | answering well depends on something the user said before (typically at the start of a conversation) | `retrieve_memories(memory_id, namespace="/users/<actor>/facts/", query=..., top_k=...)` |

Tool **docstrings are load-bearing** — with `@tool`, the docstring becomes the description
the model sees, and it decides whether and when to call a tool almost entirely from that
text. The **system prompt** reinforces it: it tells the agent it has memory tools and
*when* to use them, doing the steering the callback tutorials did in code.

## Memory-as-tool vs. the callback patterns

| | 01 — Built-in callback | 02 — Custom callback | 03 — Memory as a tool (this) |
|---|---|---|---|
| **Who decides to store** | The framework callback, every turn | Your hook, every turn | **The model**, via `store_memory` |
| **Who decides to recall** | The framework callback, every turn | Your hook, every turn | **The model**, via `recall_memory` |
| **How memory reaches the model** | Injected into the prompt by a callback | Injected by your hook | Returned as a `ToolMessage` the model requested |
| **Control flow** | Linear (recall → model → persist) | Linear | **ReAct loop** (model drives) |
| **Best when** | You want deterministic, always-on memory | …plus customized extraction | The model should manage its own memory lifecycle |

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock
  model-invocation permissions (resolved from the standard AWS chain).
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region (request it in the
  Bedrock console under *Model access*; `us-west-2` is a safe default).
- **No IAM execution role required** — built-in strategies use AgentCore-managed models.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python langgraph-ltm-memory-tool.py
```

Expected output: in **Session 1** the user shares their name, dietary restrictions, and a
training goal, and the agent calls `store_memory` (watch for `🧠 store_memory` log lines)
as those facts come up. After a wait for extraction, **Session 2** starts with a fresh
message list; the agent calls `recall_memory` (watch for the `🔎 recall_memory` log line)
and uses the recovered facts to suggest a vegetarian, high-protein, shellfish-free dinner —
despite having no in-session history. The script then deletes the memory resource.

> **Note on timing:** extraction is asynchronous. A fact saved in one turn is **not**
> instantly searchable in the next — that's why the demo seeds memory in Session 1, waits,
> then recalls in Session 2. The script polls for up to ~2 minutes; if records haven't
> surfaced it warns and continues (they typically appear shortly after).

## Key implementation notes

- **The model owns the lifecycle.** Storage and recall happen only when the model emits a
  tool call. LangGraph's `ToolNode` dispatches it; AgentCore does the work. There is no
  code path that stores or recalls on the model's behalf.
- **Tools close over `memory_id` and `session_id`.** A factory (`build_memory_tools`)
  binds the IDs so the `@tool` functions stay simple (`store_memory(fact)` /
  `recall_memory(query)`) — exactly the shape the model fills in. Rebuild the tools per
  session to bind a new `session_id`.
- **Store and recall are separated by extraction latency.** `store_memory` queues a fact
  for asynchronous Semantic extraction; `recall_memory` reads the distilled records. They
  are not instantaneous round-trips — design conversations (and the demo's two sessions)
  accordingly.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept
  wildcards — `recall_memory` substitutes `{actorId}` itself before calling.
- **Same actor, different session.** Session 2 uses a new `session_id` but the same
  `ACTOR_ID`, so `recall_memory` reads the same `/users/<actor>/facts/` namespace the
  facts were extracted into.
- **Built-in strategies need no IAM role.** To swap in your own models or prompts, attach
  a custom-override strategy as in [`02-custom-callback/`](../02-custom-callback/).
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if
  a turn raises. Comment out the delete block to keep the memory between runs.

## Where to go next

- The built-in callback pattern (framework-managed lifecycle):
  [`../01-built-in-callback/`](../01-built-in-callback/)
- The custom callback pattern (hand-rolled hooks + custom strategy):
  [`../02-custom-callback/`](../02-custom-callback/)
- The long-term memory overview (all strategies, retrieval, namespaces):
  [`../../../../README.md`](../../../../README.md)
- The same pattern with other frameworks:
  [`../../with-claude-sdk/03-memory-as-tool/`](../../with-claude-sdk/03-memory-as-tool/),
  [`../../with-strands-agent/03-memory-tool/`](../../with-strands-agent/03-memory-tool/),
  [`../../with-llamaindex-agent/03-memory-tool/`](../../with-llamaindex-agent/03-memory-tool/)
