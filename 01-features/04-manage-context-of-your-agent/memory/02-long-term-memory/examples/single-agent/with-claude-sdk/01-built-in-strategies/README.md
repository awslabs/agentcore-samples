# Long-term memory — Claude SDK, built-in strategies

A conversation loop built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), wired to AgentCore **long-term memory** using **built-in strategies**.

Short-term memory (see the [`01-short-term-memory`](../../../../../01-short-term-memory/) examples) stores raw conversation turns verbatim. Long-term memory goes a step further: it runs an **asynchronous extraction pipeline** over those turns and distills them into reusable, searchable records — standalone **facts** (Semantic strategy) and stable **user preferences** (User Preference strategy). You don't call a separate "extract" API; attaching strategies to the memory resource makes every `create_event` feed the pipeline automatically.

Because the Anthropic SDK is a stateless API client — no framework, no hooks, no session handling — the full memory lifecycle is explicit and easy to follow: create with strategies → store turns → wait for extraction → retrieve records → inject them into a future session's system prompt.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Personal assistant |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) + User Preference — both **built-in** (no IAM execution role) |
| Memory components | `create_memory_and_wait` (with strategies), `create_event`, `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Complexity | Intermediate |

## What it does

[`claude-sdk-ltm-semantic.py`](./claude-sdk-ltm-semantic.py):

1. Creates (or reuses) a memory resource with **two built-in strategies** — Semantic and User Preference. No IAM execution role is required.
2. Runs a first conversation with Claude through Amazon Bedrock, maintaining the `messages[]` array by hand and storing each turn with `create_event` — which queues the turn for long-term extraction.
3. Polls until the asynchronous extraction surfaces records (~30–90s).
4. Retrieves the distilled facts and preferences with `retrieve_memories` and prints them.
5. Starts a **second session with an empty `messages[]` array**, injecting the retrieved long-term records into the system prompt — and shows the agent still recalls the user's name, dietary needs, and interests.
6. Deletes the memory resource in a `finally` block.

## Architecture

```
  ┌──────────────┐                              ┌─────────────────────────────┐
  │  Your code   │ ──── 1. create_event ──────▶ │  AgentCore Memory           │
  │ (messages[]) │       (each turn)            │                             │
  │              │                              │  short-term events ──┐      │
  │              │                              │                      │      │
  │              │                              │   2. async extraction│      │
  │              │                              │      (built-in       ▼      │
  │              │                              │       strategies) long-term │
  │              │ ◀─── 3. retrieve_memories ── │   • Semantic facts   records│
  │              │       (per namespace)        │   • User preferences        │
  └──────┬───────┘                              └─────────────────────────────┘
         │
         │ 4. inject records into system prompt, then messages.create(...)
         ▼
  ┌──────────────┐
  │ Claude via   │  5. assistant reply, now personalized from long-term memory
  │ Amazon       │
  │ Bedrock      │
  └──────────────┘
```

The lifecycle is just a handful of calls, because the SDK gives you nowhere else to hang them:

| Step | Where | How |
|---|---|---|
| **Create** | Once, at startup | `create_memory_and_wait(name=..., strategies=[{semanticMemoryStrategy: {...}}, {userPreferenceMemoryStrategy: {...}}])` |
| **Store** | After each turn | `create_event(memory_id, actor_id, session_id, messages=[(text, "USER"), (text, "ASSISTANT")])` — extraction is triggered automatically |
| **Wait** | Before first retrieval | Poll `retrieve_memories` until records appear (extraction is asynchronous) |
| **Retrieve** | On resume / new session | `retrieve_memories(memory_id, namespace="/users/<actor>/facts/", query=..., top_k=...)` |
| **Inject** | Building the next prompt | Fold retrieved `content.text` records into the `system` prompt |

## The strategies used here

| Strategy | `StrategyType` value | Extracts | Namespace in this tutorial |
|---|---|---|---|
| **Semantic** | `semanticMemoryStrategy` | Standalone facts about the user/world | `/users/{actorId}/facts/` |
| **User Preference** | `userPreferenceMemoryStrategy` | Stable, durable user preferences | `/users/{actorId}/preferences/` |

`{actorId}` is substituted at extraction time, keeping each user's records isolated. Two other built-in strategies exist — **Summary** (`summaryMemoryStrategy`, rolling per-session summaries) and **Episodic** (`episodicMemoryStrategy`, meaningful interaction sequences) — see the [long-term memory README](../../../../README.md).

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

python claude-sdk-ltm-semantic.py
```

Expected output: a first conversation where the user shares their name, travel plans, dietary preference, and interests; a wait while extraction runs; the extracted facts and preferences printed back; then a "second session" — starting with an empty `messages[]` array — where the agent personalizes its suggestion using only the long-term memory injected into the system prompt. The script then deletes the memory resource.

> **Note on timing:** extraction is asynchronous. The script polls for up to ~2 minutes; if records haven't surfaced by then it warns and continues (they typically appear shortly after). Re-running retrieval a little later will pick them up. This is expected behavior, not an error.

## Key implementation notes

- **Strategies are what make memory "long-term."** The `create_event` call is identical to the short-term tutorial — attaching strategies to the memory resource is the only difference, and it's what feeds the extraction pipeline.
- **Built-in strategies need no IAM role.** AgentCore manages the extraction/consolidation models. To swap in your own models or prompts, use the strategy-override examples under `02-long-term-memory`.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept wildcards — substitute `{actorId}` yourself before calling.
- **The SDK is stateless, so memory is injected via the prompt.** Across sessions, a Claude agent "remembers" by retrieving long-term records and folding them into the `system` prompt — there is no server-side session on the Anthropic side.
- **Extraction is asynchronous.** Records appear ~30–90s after `create_event`. Don't retrieve immediately; in production you'd retrieve on the user's next interaction, by which point extraction has completed.
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a turn raises. Comment out the delete block to keep the memory between runs — `get_or_create_memory` will reuse it by name.

## Where to go next

- The built-in strategy primitives and AWS CLI walkthrough: [`../../../../01-built-in-strategies/`](../../../../01-built-in-strategies/)
- The long-term memory overview (all strategies, retrieval, namespaces): [`../../../../README.md`](../../../../README.md)
- The same patterns in a framework: [`../../with-strands-agent/`](../../with-strands-agent/)
- Short-term memory with the Claude SDK: [`../../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/`](../../../../../01-short-term-memory/examples/single-agent/with-claude-sdk/)
