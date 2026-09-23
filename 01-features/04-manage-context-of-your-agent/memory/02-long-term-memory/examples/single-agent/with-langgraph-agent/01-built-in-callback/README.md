# Long-term memory — LangGraph, built-in callback

A LangGraph agent whose **long-term memory is wired in by the framework**, using the
[`langgraph-checkpoint-aws`](https://pypi.org/project/langgraph-checkpoint-aws/) package's
two AgentCore integrations ([source](https://github.com/langchain-ai/langchain-aws/tree/main/libs/langgraph-checkpoint-aws),
in the [`langchain-aws`](https://github.com/langchain-ai/langchain-aws) monorepo):

- **`AgentCoreMemorySaver`** — a LangGraph **checkpointer** that persists and resumes
  conversation state automatically, keyed by `thread_id` (session) and `actor_id`.
- **`AgentCoreMemoryStore`** — a LangGraph **store** backed by AgentCore long-term
  strategies. Two small middleware callbacks (`@dynamic_prompt` to recall, `@after_model`
  to persist) are the entire memory wiring — the store turns `store.put` into a
  `create_event` and `store.search` into a `retrieve_memories`, and the AgentCore
  **Semantic strategy** extracts durable facts asynchronously in the background.

This is the **lowest-effort** integration: you declare *where* memory hooks into the
graph and the package does the rest. You never call `create_event` / `retrieve_memories`
yourself. Contrast with [`02-custom-callback/`](../02-custom-callback/), where the
callbacks hand-roll retrieval, context injection, and storage.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent usecase | Nutrition assistant |
| Framework | LangGraph (v1.0 `create_agent` + middleware) |
| LLM model | Claude Haiku 4.5 — `global.anthropic.claude-haiku-4-5-20251001-v1:0` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in** (no IAM execution role) |
| Memory components | `AgentCoreMemorySaver`, `AgentCoreMemoryStore`, `create_memory_and_wait`, `retrieve_memories`, `delete_memory_and_wait` |
| Callback components | `@dynamic_prompt` (recall), `@after_model` (persist), `context_schema` (runtime identity) |
| Complexity | Intermediate |

> ### ⚠️ LangGraph v1.0 API note
> LangGraph v1.0 **deprecated** `langgraph.prebuilt.create_react_agent` together with its
> `pre_model_hook` / `post_model_hook` arguments. The current API is
> **`from langchain.agents import create_agent`** with the **middleware** system
> (`@before_model`, `@after_model`, `@dynamic_prompt`, `@wrap_model_call`) from
> `langchain.agents.middleware`. **This tutorial uses the current API.** The sibling
> [`02-custom-callback/`](../02-custom-callback/) examples still use the deprecated
> `create_react_agent` + pre/post hooks; both styles work today, but new code should
> prefer middleware.
>
> One consequence: middleware callbacks receive `(state, runtime)` — **not** the
> invocation `config`. So per-user identity (`actor_id`, `session_id`) travels in a typed
> **runtime context** (`context_schema=MemoryContext`), supplied at invoke time with
> `context=...`. The checkpointer still reads `thread_id` / `actor_id` from
> `config["configurable"]` as it always has — so each `invoke` passes **both** `config`
> and `context`.

## What it does

[`langgraph-ltm-built-in-callback.py`](./langgraph-ltm-built-in-callback.py):

1. Creates (or reuses) a memory resource with a built-in **Semantic** strategy
   (namespace `/users/{actorId}/facts/`). No IAM execution role required.
2. Builds the agent with `create_agent`, passing the `AgentCoreMemoryStore`, the
   `AgentCoreMemorySaver` checkpointer, two middleware callbacks, and `context_schema`.
3. Runs **Session 1**: the user shares durable facts (vegetarian, shellfish allergy,
   half-marathon training). `@after_model` persists each turn to the store automatically.
4. Polls until the asynchronous Semantic extraction surfaces records (~30–90s).
5. Runs **Session 2**: a brand-new session (new `thread_id`). `@dynamic_prompt` recalls
   the facts from session 1 and folds them into the system prompt, so the agent suggests
   a vegetarian, high-protein, shellfish-free dinner — with no short-term history.
6. Deletes the memory resource in a `finally` block.

## Architecture

```
  ┌───────────────────────────── create_agent (LangGraph v1.0) ─────────────────────────────┐
  │                                                                                          │
  │   @dynamic_prompt  ──▶  model (Bedrock)  ──▶  @after_model  ──▶  (tools, if any)          │
  │   (inject recalled        Claude Haiku        (persist the                                │
  │    facts into the          4.5                 turn for                                   │
  │    system prompt)                              extraction)                                │
  │        │                                            │                                     │
  └────────┼────────────────────────────────────────────┼─────────────────────────────────────┘
           │ store.search(("users", actor, "facts/"))    │ store.put((actor, session), msg)
           ▼                                              ▼
  ┌──────────────────────────────────────────────────────────────────────────────────────────┐
  │  AgentCore Memory  (one resource)                                                          │
  │                                                                                            │
  │   AgentCoreMemoryStore   ── create_event ─▶ short-term events ─▶ async Semantic extraction  │
  │                          ◀─ retrieve_memories ── long-term records in /users/{actor}/facts/ │
  │                                                                                            │
  │   AgentCoreMemorySaver   ── checkpoints graph state per (thread_id, actor_id)  [automatic]  │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
```

## The two callbacks

| Callback | Decorator | When it runs | What it does |
|---|---|---|---|
| **`inject_recalled_facts`** | `@dynamic_prompt` | before the model, every turn | `store.search` the user's Semantic namespace with the latest message; append any recalled facts to the system prompt |
| **`persist_turn`** | `@after_model` | after the model responds | `store.put` the newest human + AI messages; the store's `create_event` feeds asynchronous Semantic extraction |

The checkpointer (`AgentCoreMemorySaver`) needs **no** callback — passing it to
`create_agent(checkpointer=...)` makes state persistence fully automatic.

## Checkpointer vs. store (two distinct jobs)

| | `AgentCoreMemorySaver` (checkpointer) | `AgentCoreMemoryStore` (store) |
|---|---|---|
| **Persists** | Full graph **state** (the running conversation) | Individual **messages** for long-term extraction |
| **Scope** | Per `(thread_id, actor_id)` — one session/thread | Cross-session, per `actor_id` namespace |
| **Purpose** | Resume a conversation exactly where it left off | Recall durable facts in a *different* session |
| **Wiring** | `create_agent(checkpointer=...)` — automatic | `create_agent(store=...)` + the two callbacks |
| **Reads identity from** | `config["configurable"]` (`thread_id`, `actor_id`) | `runtime.context` (`actor_id`, `session_id`) |

They are complementary: the saver gives you short-term continuity within a session; the
store + Semantic strategy gives you durable knowledge across sessions.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock
  model-invocation permissions (resolved from the standard AWS chain).
- **Amazon Bedrock model access for Claude Haiku 4.5** in your region (request it in the
  Bedrock console under *Model access*; `us-west-2` is a safe default).
- **No IAM execution role required** — built-in strategies use AgentCore-managed models
  for extraction and consolidation.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python langgraph-ltm-built-in-callback.py
```

Expected output: in **Session 1** the user shares facts and you'll see `🧠 persisted
turn` log lines. After a wait for extraction, **Session 2** starts on a new thread; watch
for `🔎 recalled N long-term fact(s)`, and the agent answers with a vegetarian,
high-protein, shellfish-free suggestion. The script then deletes the memory resource.

> **Note on timing:** extraction is asynchronous. A fact saved in one turn is **not**
> instantly searchable in the next — that's why the demo seeds memory in Session 1,
> waits, then recalls in Session 2. The script polls for up to ~2 minutes; if records
> haven't surfaced it warns and continues (they typically appear shortly after).

## Key implementation notes

- **The framework owns the lifecycle.** You declare callbacks and integrations; the
  package translates `store.put`/`store.search` into AgentCore API calls and the Semantic
  strategy extracts facts in the background. No direct `create_event`/`retrieve_memories`.
- **Identity travels in `runtime.context`, not `config`.** v1.0 middleware gets
  `(state, runtime)`. We declare `MemoryContext(actor_id, session_id)` as `context_schema`
  and pass an instance at `invoke(..., context=...)`. The checkpointer separately reads
  `thread_id`/`actor_id` from `config["configurable"]`.
- **Store write namespaces are 2-tuples.** `store.put` requires `(actor_id, session_id)`
  (verified in the package source). Search namespaces become `"/" + "/".join(tuple)`, so
  `("users", actor, "facts/")` matches the strategy template `/users/{actorId}/facts/`.
- **Recalled records live under `item.value["content"]`.** The store maps an AgentCore
  memory record into a LangGraph `Item` whose `value["content"]` holds the extracted text.
- **Failures never break a turn.** Both callbacks catch exceptions and continue without
  memory rather than crashing the conversation.
- **Built-in strategies need no IAM role.** To customize extraction prompts/models, see
  [`02-custom-callback/`](../02-custom-callback/), which attaches a custom-override strategy.
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if
  a turn raises. Comment out the delete block to keep it between runs.

## Where to go next

- The custom-callback pattern (hand-rolled hooks + custom strategy):
  [`../02-custom-callback/`](../02-custom-callback/)
- Memory as tools the model invokes: [`../03-memory-as-tool/`](../03-memory-as-tool/)
- The long-term memory overview (all strategies, retrieval, namespaces):
  [`../../../../README.md`](../../../../README.md)
- The same pattern with the Claude SDK:
  [`../../with-claude-sdk/01-built-in-strategies/`](../../with-claude-sdk/01-built-in-strategies/)
