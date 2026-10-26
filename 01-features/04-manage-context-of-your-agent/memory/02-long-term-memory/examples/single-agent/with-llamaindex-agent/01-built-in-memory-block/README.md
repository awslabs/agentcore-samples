# LlamaIndex + AgentCore memory — built-in memory block (long-term)

The idiomatic way to give a LlamaIndex agent long-term memory: a custom **`BaseMemoryBlock`**
wired into LlamaIndex's **`Memory`** class, backed by Amazon Bedrock AgentCore. Instead of
exposing memory as a tool the LLM must remember to call (that's
[`../03-memory-tool/`](../03-memory-tool/)), the block plugs into the agent's memory
**lifecycle** — the framework calls it automatically.

| Information | Details |
|---|---|
| Tutorial type | Long-term, single-agent |
| Agent usecase | Personal Knowledge Assistant (remembers user facts across sessions) |
| Framework | LlamaIndex (`Memory` + `BaseMemoryBlock`, `FunctionAgent`) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in** (no IAM execution role) |
| Memory components | `BaseMemoryBlock` wrapping `create_event` (put) + `search_long_term_memories` (get) |
| Complexity | Advanced |

> ### ⚠️ LlamaIndex API note — `Memory` + `BaseMemoryBlock` (NOT `ChatMemoryBuffer`)
> `ChatMemoryBuffer` is **deprecated**. The current integration point is the **`Memory`**
> class (`llama_index.core.memory.Memory`) composed of one or more **`BaseMemoryBlock`**
> subclasses. A block implements two async hooks — `_aget` (retrieve, every turn) and
> `_aput` (persist, on short-term flush) — plus an optional `atruncate`. Verified against
> `llama-index-core` 0.14.x.

## How the block maps onto AgentCore

| `BaseMemoryBlock` hook | When the framework calls it | AgentCore call |
|---|---|---|
| `_aget(messages)` | **Every turn**, before the LLM runs | `search_long_term_memories(query, namespace)` |
| `_aput(messages)` | When short-term history **flushes** | `create_event` (via `add_turns`) |
| `atruncate(content, n)` | When assembled memory is too big | in-memory string trim (no AgentCore call) |

### The one lifecycle gotcha

Blocks do **not** receive every message. `_aput` only fires for messages **ejected from the
short-term FIFO** when it exceeds its token budget (`from_short_term_memory=True`, verified
in `llama_index/core/memory/memory.py`). Flushing is driven by the **put path**: each
`agent.run()` puts messages and `_manage_queue()` ejects the oldest ones once the queue
passes `chat_history_token_ratio * token_limit`. `aget()` is **read-only** and does **not**
flush. To make this deterministic in a short demo the script sets a small `token_limit`
(800) and then sends a couple of short "wind-down" turns at the end of session 1 to push the
last fact-bearing turns past the flush threshold before waiting for extraction. In
production you'd use a realistic limit and persistence happens naturally as the conversation
grows.

## Architecture

```
                        LlamaIndex FunctionAgent
                                  │
                   agent.run(msg, memory=Memory)
                                  │
              ┌───────────────────┴────────────────────┐
              │            LlamaIndex Memory            │
              │  short-term FIFO (token-bounded queue)  │
              │                  │ flush (over budget)  │
              │                  ▼                      │
              │     AgentCoreMemoryBlock (BaseMemoryBlock)
              └──────────┬──────────────────┬───────────┘
                 _aget    │                  │  _aput
        search_long_term_memories      create_event
                         │                  │
                         ▼                  ▼
           ┌──────────────────────────────────────────┐
           │       AgentCore Memory (one memory_id)     │
           │  Semantic strategy →                       │
           │    /llamaindex-ltm/{actorId}/facts/        │  ◀── write AND read here
           └──────────────────────────────────────────┘
```

## ✅ Namespace correctness (read this)

The write path (Semantic extraction) and the read path (`search_long_term_memories`) target
the **same resolved namespace**. The script defines one template and resolves it once:

```python
NAMESPACE_TEMPLATE = "/llamaindex-ltm/{actorId}/facts/"
RESOLVED_NAMESPACE  = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)
# strategy "namespaces": [NAMESPACE_TEMPLATE]   ← writes here
# _aget search namespace_prefix=RESOLVED_NAMESPACE ← reads here
```

This avoids a bug present in the older memory-as-tool tutorials, which searched a hard-coded
`/strategies/` prefix that did **not** match where the Semantic strategy actually wrote — so
retrieval silently returned nothing. **Always read from the same namespace your strategy
writes to.**

## What it does

[`llamaindex-ltm-built-in-memory-block.py`](./llamaindex-ltm-built-in-memory-block.py):

1. Creates (or reuses) one memory resource with a built-in **Semantic** strategy whose
   namespace template is `/llamaindex-ltm/{actorId}/facts/`. No IAM execution role.
2. **Session 1** teaches the assistant facts about the user (language, allergy, trip,
   hobbies). The small token budget flushes those turns into AgentCore via the block's
   `_aput`; the Semantic strategy extracts durable records.
3. Waits ~90s for asynchronous extraction.
4. **Session 2** uses a *fresh* `Memory` (empty short-term FIFO), so the only way the
   assistant can answer recall questions is the block's `_aget` reading AgentCore long-term
   memory.
5. Deletes the memory resource in a `finally` block.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model access
- IAM permissions: `bedrock-agentcore:CreateMemory`, `:DeleteMemory`, `:GetMemory`,
  `:CreateEvent`, `:RetrieveMemoryRecords`
- Amazon Bedrock model access for **Claude Sonnet 4.6** in your region (`us-west-2` is a
  safe default)
- **No IAM execution role required** — built-in strategies use AgentCore-managed models.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python llamaindex-ltm-built-in-memory-block.py
```

Expected output: Session 1 prints each turn and a `🧠 Persisted … to AgentCore` line as the
FIFO flushes; then `⏳ Waiting …` for extraction; then Session 2 recalls the Rust + peanut
allergy, Lisbon + vegetarian, and classical-guitar facts — none of which were stated in
Session 2.

## Key implementation notes

- **The agent has no "remember" tool.** Persistence and recall are automatic via the block's
  lifecycle hooks — that's the whole point versus the memory-as-tool pattern.
- **Retrieval failures are non-fatal.** `_aget` logs and returns `""` on error, so a
  transient memory issue degrades gracefully instead of breaking the turn.
- **`priority=0`** on the block means it's never truncated out of context.
- **`MemorySessionManager` is a `PrivateAttr`,** created lazily — it isn't a pydantic value,
  so the block stays clonable/serialisable.
- **Cleanup runs in `finally`.** The resource is billable; comment out the delete block to
  keep it between runs.

## Where to go next

- The simpler memory-as-tool pattern: [`../03-memory-tool/`](../03-memory-tool/)
- A more sophisticated block (conditional storage, scored retrieval):
  [`../02-custom-memory-block/`](../02-custom-memory-block/)
- The long-term memory overview (all strategies, retrieval, namespaces):
  [`../../../../README.md`](../../../../README.md)
