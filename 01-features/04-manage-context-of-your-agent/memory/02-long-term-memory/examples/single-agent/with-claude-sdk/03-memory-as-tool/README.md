# Long-term memory — Claude SDK, memory as a tool

A conversation loop built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), where AgentCore **long-term memory** is exposed as **tools the model decides to call**.

The two companion tutorials wired memory *around* the model. [`01-built-in-strategies`](../01-built-in-strategies/) stored every turn and injected retrieved records into the system prompt; [`02-custom-strategy-override`](../02-custom-strategy-override/) did the same with a customized extraction pipeline. In both, **your code** decided when to save and when to recall. Here we hand that decision to the agent: we define `store_memory` and `recall_memory`, pass them to Claude via the `tools=` parameter, and run the standard **agentic loop** — the model chooses, on its own, when to persist a durable fact and when to search its past knowledge.

Because the Anthropic SDK is a stateless API client — no framework, no hooks, no session handling — the entire loop is explicit. That makes this the clearest possible view of the memory-as-tool pattern: the model asks (via `tool_use` blocks), your loop dispatches to the tool implementations, and AgentCore stores (`create_event`) and retrieves (`retrieve_memories`).

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Personal assistant |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in** (no IAM execution role) |
| Memory components | `create_memory_and_wait` (with a Semantic strategy), `create_event`, `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Tool components | `tools=` parameter, `tool_use` / `tool_result` agentic loop (per Anthropic's spec) |
| Complexity | Advanced |

## What it does

[`claude-sdk-ltm-memory-tool.py`](./claude-sdk-ltm-memory-tool.py):

1. Creates (or reuses) a memory resource with a built-in **Semantic** strategy. No IAM execution role is required.
2. Declares two tools — `store_memory` and `recall_memory` — and passes them to Claude on every `messages.create` call.
3. Runs **Session 1**: a multi-turn conversation where the user shares durable facts (name, dietary restrictions, a training goal). The agent calls `store_memory` on its own as those facts come up — backed by `create_event`.
4. Polls until the asynchronous extraction surfaces records (~30–90s).
5. Runs **Session 2**: a brand-new conversation with an **empty `messages[]` array** — no short-term history. The user asks something that depends on the past, and the agent calls `recall_memory` itself (backed by `retrieve_memories`) to recover the context before answering.
6. Deletes the memory resource in a `finally` block.

Crucially, the demo never injects memory into the prompt and never calls `create_event` / `retrieve_memories` directly in the conversation flow — **every memory operation happens because the model asked for it.**

## Architecture

```
  ┌──────────────────────────────────────────────────────────────────────────┐
  │  Agentic loop (your code)                                                  │
  │                                                                            │
  │   messages.create(tools=[store_memory, recall_memory]) ──▶ Claude (Bedrock)│
  │                          ▲                                      │          │
  │                          │                                      ▼          │
  │             tool_result  │                            stop_reason ==       │
  │             (USER turn)  │                              "tool_use"?        │
  │                          │                                      │ yes      │
  │                          │                                      ▼          │
  │                          │                       ┌──────────────────────┐  │
  │                          └───────────────────────┤ dispatch tool_use:   │  │
  │                                                  │  • store_memory      │  │
  │                                                  │  • recall_memory     │  │
  │                                                  └──────────┬───────────┘  │
  └─────────────────────────────────────────────────────────────┼────────────┘
                                                                  │
             store_memory → create_event                         │
             recall_memory → retrieve_memories                   ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │  AgentCore Memory                                                        │
  │   create_event ──▶ short-term events ──▶ async Semantic extraction ──▶   │
  │   long-term records ◀── retrieve_memories (semantic search by namespace) │
  └────────────────────────────────────────────────────────────────────────┘
```

## The agentic loop (per Anthropic's tool-use spec)

The loop in `run_agent` follows the Messages API tool-use contract exactly:

| Step | What happens |
|---|---|
| **1. Call** | `messages.create(model=..., tools=TOOLS, messages=...)` |
| **2. Echo** | Append the assistant's **full** `response.content` to `messages` — this preserves the `tool_use` blocks the next request must match |
| **3. Done?** | If `stop_reason != "tool_use"`, the model produced its final answer — return the text |
| **4. Execute** | For each `tool_use` block, run the tool and build one `tool_result` (matched by `tool_use_id`); set `is_error: true` if it failed |
| **5. Feed back** | Append all `tool_result` blocks as a single `user` message, then loop |

The loop is bounded by `MAX_TOOL_ITERATIONS` as a guardrail. A well-behaved model finishes in 1–3 iterations.

## The tools

| Tool | Model calls it when… | Backed by |
|---|---|---|
| **`store_memory`** | the user shares a durable fact worth remembering across sessions (name, preferences, goals, constraints) | `create_event(memory_id, actor_id, session_id, messages=[(fact, "USER")])` — feeds the Semantic extraction pipeline |
| **`recall_memory`** | answering well depends on something the user said before (typically at the start of a conversation) | `retrieve_memories(memory_id, namespace="/users/<actor>/facts/", query=..., top_k=...)` |

Tool **descriptions are load-bearing** — Claude decides whether and when to call a tool almost entirely from its description, so each one states its trigger conditions explicitly. The **system prompt** reinforces this: it tells the agent it has memory tools and *when* to use them, doing the steering that tutorials 01/02 did in code.

## Memory-as-tool vs. the other two patterns

| | 01 — Built-in (post-response) | 02 — Custom override | 03 — Memory as a tool (this) |
|---|---|---|---|
| **Who decides to store** | Your code, after every turn | Your code, after every turn | **The model**, via `store_memory` |
| **Who decides to recall** | Your code, at session start | Your code, at session start | **The model**, via `recall_memory` |
| **How memory reaches the model** | Injected into the system prompt | Injected into the system prompt | Returned as a `tool_result` the model requested |
| **Control flow** | Linear (store → retrieve → prompt) | Linear | **Agentic loop** (model drives) |
| **Best when** | You want deterministic, always-on memory | …plus customized extraction | The model should manage its own memory lifecycle |

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model-invocation permissions. The `AnthropicBedrock` client and `MemoryClient` both resolve credentials from the standard AWS chain (environment variables, `~/.aws/credentials`, or an instance/role profile).
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region. Request it in the Bedrock console under *Model access*. (Model availability varies by region; `us-west-2` is a safe default.)
- No IAM execution role is required — built-in strategies use AgentCore-managed models for extraction and consolidation.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python claude-sdk-ltm-memory-tool.py
```

Expected output: in **Session 1** the user shares their name, dietary restrictions, and a training goal, and the agent calls `store_memory` (watch for `🧠 store_memory` log lines) as those facts come up. After a wait for extraction, **Session 2** starts with an empty `messages[]` array; the agent calls `recall_memory` (watch for the `🔎 recall_memory` log line) and uses the recovered facts to suggest a vegetarian, high-protein, shellfish-free dinner — despite having no short-term history. The script then deletes the memory resource.

> **Note on timing:** extraction is asynchronous. A fact saved in one turn is **not** instantly searchable in the next — that's why the demo seeds memory in Session 1, waits, then recalls in Session 2. The script polls for up to ~2 minutes; if records haven't surfaced by then it warns and continues (they typically appear shortly after). This is expected behavior, not an error.

## Key implementation notes

- **The model owns the lifecycle.** Storage and recall happen only when the model emits a `tool_use` block. Your loop dispatches it; AgentCore does the work. There is no code path that stores or recalls on the model's behalf.
- **Echo `response.content` verbatim.** Appending the assistant's full content (text + `tool_use` blocks) is what lets the API match your `tool_result` blocks on the next request. Extracting only the text would break the loop.
- **One `tool_result` per `tool_use`, matched by `tool_use_id`.** The API rejects the follow-up if any `tool_use` block lacks a matching result.
- **Errors go back to the model, not up the stack.** `dispatch_tool` catches exceptions and returns an error `tool_result` (`is_error: true`) so the model can adapt — retry, ask the user, or proceed without the tool.
- **Store and recall are separated by extraction latency.** `store_memory` queues a fact for asynchronous Semantic extraction; `recall_memory` reads the distilled records. They are not instantaneous round-trips — design conversations (and the demo's two sessions) accordingly.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept wildcards — `recall_memory` substitutes `{actorId}` itself before calling.
- **Built-in strategies need no IAM role.** AgentCore manages the extraction/consolidation models. To swap in your own models or prompts, see [`02-custom-strategy-override`](../02-custom-strategy-override/).
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a turn raises. Comment out the delete block to keep the memory between runs — `get_or_create_memory` will reuse it by name.

## Where to go next

- The other two integration patterns: [`01-built-in-strategies`](../01-built-in-strategies/), [`02-custom-strategy-override`](../02-custom-strategy-override/)
- The long-term memory overview (all strategies, retrieval, namespaces): [`../../../../README.md`](../../../../README.md)
- The same pattern in a framework: [`../../with-strands-agent/03-memory-tool/`](../../with-strands-agent/03-memory-tool/), [`../../with-llamaindex-agent/03-memory-tool/`](../../with-llamaindex-agent/03-memory-tool/)
- Short-term memory with the Claude SDK: [`../../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/`](../../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/)
