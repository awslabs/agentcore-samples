# Short-term memory — Claude SDK single-agent

A conversation loop built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), wired to AgentCore **short-term memory**.

Unlike the Strands, LangGraph, and LlamaIndex examples in the sibling folders, there is **no agent framework here**. The Anthropic SDK is a stateless API client — it has no built-in conversation management, hooks, or session handling. You own the `messages[]` array, and you decide exactly when to read from and write to memory. That makes this the most explicit illustration of what AgentCore short-term memory does: **persist raw conversation turns so a new process can resume the conversation.**

| Information | Details |
|---|---|
| Tutorial type | Short-term conversational |
| Agent type | Personal assistant |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Memory components | `create_event`, `get_last_k_turns`, `list_memories`, `delete_memory_and_wait` |
| Complexity | Beginner |

## What it does

[`claude-sdk-stm-conversation.py`](./claude-sdk-stm-conversation.py):

1. Creates (or reuses) a strategy-less AgentCore memory resource — short-term, raw events only.
2. Runs a multi-turn conversation with Claude through Amazon Bedrock, maintaining the `messages[]` array by hand.
3. Stores each completed user/assistant exchange as an event with `create_event`.
4. **Discards the in-memory conversation and rebuilds it from AgentCore** with `get_last_k_turns`, simulating the user returning in a new process — and shows the agent still remembers the user's name and interests.
5. Prints the stored turns, then deletes the memory resource in a `finally` block.

## Architecture

```
  ┌──────────────┐   1. rehydrate history    ┌────────────────────────┐
  │  Your code   │ ◀──── get_last_k_turns ────│  AgentCore Memory      │
  │ (messages[]) │                            │  (short-term / events) │
  │              │ ───── create_event ──────▶ │                        │
  └──────┬───────┘   4. store each turn       └────────────────────────┘
         │
         │ 2. messages.create(messages=[...])
         ▼
  ┌──────────────┐
  │ Claude via   │  3. assistant reply
  │ Amazon       │ ─────────────────────────┐
  │ Bedrock      │                           │
  └──────────────┘                           ▼
                                      (appended to messages[]
                                       and stored back to Memory)
```

The two integration points are just functions, because the SDK gives you nowhere else to hang them:

| Step | Where | How |
|---|---|---|
| **Retrieve** | At startup / on resume | `get_last_k_turns(...)` → translate stored turns into Anthropic `messages[]` |
| **Converse** | Each turn | `client.messages.create(model=..., system=..., messages=messages)` |
| **Store** | After each turn | `create_event(memory_id, actor_id, session_id, messages=[(text, "USER"), (text, "ASSISTANT")])` |

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model-invocation permissions. The `AnthropicBedrock` client and `MemoryClient` both resolve credentials from the standard AWS chain (environment variables, `~/.aws/credentials`, or an instance/role profile).
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region. Request it in the Bedrock console under *Model access*. (Model availability varies by region; `us-west-2` is a safe default.)

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python claude-sdk-stm-conversation.py
```

Expected output: a first conversation where the user introduces themselves, then a "user returns" section — running with a fresh `messages[]` array rebuilt only from memory — where the agent correctly recalls the earlier details. The script then prints the stored turns and deletes the memory resource.

## Key implementation notes

- **The SDK is stateless.** Every `messages.create()` call must include the full conversation history. There is no `session_id` on the Anthropic side — continuity comes entirely from AgentCore Memory plus the `messages[]` array you maintain.
- **Roles differ between the two APIs.** AgentCore stores roles as `USER` / `ASSISTANT` / `TOOL`; the Anthropic Messages API expects lowercase `user` / `assistant`. `load_history()` translates and skips any non-conversational roles.
- **`global.` model prefix** selects Bedrock's global (cross-region) inference endpoint — the default for Sonnet 4.6, with no regional pricing premium. Swap to `us.anthropic.claude-sonnet-4-6` to pin traffic to US regions (10% premium, for data-residency needs).
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a turn raises. Comment out the delete block to keep the memory between runs — `get_or_create_memory` will reuse it by name.
- **No long-term strategies here.** Short-term memory stores raw turns only. For automatic fact/summary/preference extraction across sessions, see the long-term examples.

## Where to go next

- Long-term memory with strategies + `retrieve_memories`: [`../../../../02-long-term-memory/`](../../../../02-long-term-memory/)
- The short-term memory primitives (events, sessions, branching): [`../../../`](../../../)
- Other framework integrations: [`with-strands-agent/`](../with-strands-agent/), [`with-langgraph-agent/`](../with-langgraph-agent/), [`with-llamaindex-agent/`](../with-llamaindex-agent/)
