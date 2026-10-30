# Long-term memory — Claude SDK, Episodic strategy

A debugging assistant built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), wired to AgentCore **long-term memory** using the built-in **Episodic** strategy.

The earlier built-in-strategies tutorial used **Semantic** memory to distill isolated *facts* about the user. The Episodic strategy is different: it captures whole **episodes** — meaningful, multi-turn interaction sequences that hang together as one event ("debugged a memory leak in the payment service on Tuesday") — and adds a **Reflection** step that derives patterns *across* episodes. That's the distinction this tutorial is built to demonstrate:

- **Semantic** answers *"what facts do I know about this user?"* → "The user runs a Python payment service."
- **Episodic** answers *"what happened last time, and how did it go?"* → "Last Tuesday we chased a memory leak; root cause was an unbounded cache with no TTL; fixed in v2.4.1." — plus reflections like "unbounded caches are a recurring leak source for this team."

Because the Anthropic SDK is a stateless API client — no framework, no hooks, no session handling — the full memory lifecycle is explicit and easy to follow: create with the Episodic strategy → run multiple sessions (each one episode) → wait for the pipeline → retrieve episodes → inject them into a future session's system prompt.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Debugging assistant |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategy | Episodic — **built-in** (`episodicMemoryStrategy`, no IAM execution role) |
| Memory components | `create_memory_and_wait` (Episodic strategy), `get_memory_strategies`, `create_event` (per session), `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Complexity | Intermediate |

## What it does

[`claude-sdk-ltm-episodic.py`](./claude-sdk-ltm-episodic.py):

1. Creates (or reuses) a memory resource with the **built-in Episodic strategy** — no IAM execution role required — and **verifies at runtime** that an Episodic strategy is actually configured (see "Verifying the strategy" below).
2. Runs **two distinct debugging sessions** with Claude through Amazon Bedrock. Each session is one **episode**: a multi-turn flow (symptom → hypothesis → isolation → fix), persisted as a single `create_event` so the episode boundary stays clean.
3. Polls until the asynchronous **Extraction → Consolidation → Reflection** pipeline surfaces episode records (~60–120s; reflection can take longer).
4. Retrieves the consolidated episodes with `retrieve_memories` and prints them.
5. Starts a **third session** for a new-but-similar bug, injecting the retrieved episodes into the system prompt — and shows the agent recalling *what happened last time* (the unbounded-cache root cause) rather than investigating cold.
6. Deletes the memory resource in a `finally` block.

## Architecture

```
  ┌──────────────┐    1. create_event (per session)     ┌─────────────────────────────┐
  │  Your code   │ ───  each multi-turn session  ──────▶ │  AgentCore Memory           │
  │ (messages[]) │      = ONE episode                    │                             │
  │              │                                       │  short-term events ──┐      │
  │  session A ──┤                                       │                      │      │
  │  session B ──┤                                       │  2. async episodic   ▼      │
  │  session C ──┤                                       │     pipeline:               │
  │              │                                       │     Extraction →            │
  │              │                                       │     Consolidation →         │
  │              │ ◀── 3. retrieve_memories ──────────── │     Reflection   long-term  │
  │              │     (episodes namespace)              │   • Episodes      records   │
  └──────┬───────┘                                       │   • Reflections             │
         │                                               └─────────────────────────────┘
         │ 4. inject past episodes into system prompt, then messages.create(...)
         ▼
  ┌──────────────┐
  │ Claude via   │  5. assistant reply, now aware of HOW past sessions went
  │ Amazon       │
  │ Bedrock      │
  └──────────────┘
```

The lifecycle is a handful of calls, because the SDK gives you nowhere else to hang them:

| Step | Where | How |
|---|---|---|
| **Create** | Once, at startup | `create_memory_and_wait(name=..., strategies=[{episodicMemoryStrategy: {...}}])` |
| **Verify** | Right after create | `get_memory_strategies(memory_id)` → assert a strategy `type` contains `EPISODIC` |
| **Store** | After each session | `create_event(memory_id, actor_id, session_id, messages=[(text, role), ...])` — one event per session = one episode |
| **Wait** | Before first retrieval | Poll `retrieve_memories` until episodes appear (the pipeline is asynchronous and three-step) |
| **Retrieve** | On resume / new session | `retrieve_memories(memory_id, namespace="/episodes/<actor>/", query=..., top_k=...)` |
| **Inject** | Building the next prompt | Fold retrieved episode `content.text` into the `system` prompt |

## How episodic is *actually* configured (the truth)

This section is the heart of the tutorial. The built-in Episodic strategy is a **first-class strategy type** in the SDK — not a flavor of Semantic.

Verified directly in the SDK source, `bedrock_agentcore/memory/constants.py`:

```python
class StrategyType(Enum):
    SEMANTIC = "semanticMemoryStrategy"
    SUMMARY = "summaryMemoryStrategy"
    USER_PREFERENCE = "userPreferenceMemoryStrategy"
    EPISODIC = "episodicMemoryStrategy"     # ← the real episodic strategy
    CUSTOM = "customMemoryStrategy"
```

So configuring episodic memory is exactly this — a single-key dict whose key is `StrategyType.EPISODIC.value` (`"episodicMemoryStrategy"`):

```python
strategies = [
    {
        StrategyType.EPISODIC.value: {        # "episodicMemoryStrategy"
            "name": "DebuggingEpisodes",
            "description": "Captures complete debugging sessions as episodes ...",
            "namespaceTemplates": ["/episodes/{actorId}/"],
        }
    }
]
memory = client.create_memory_and_wait(name=..., strategies=strategies, event_expiry_days=7)
```

There is **no `add_episodic_strategy()` helper** on `MemoryClient` (unlike the semantic/preference helpers). You pass the raw `episodicMemoryStrategy` shape to `create_memory_and_wait`, which is the documented pattern in this repo's [`01-built-in-strategies/episodic.py`](../../../../01-built-in-strategies/episodic.py).

### ⚠️ The mistake this tutorial avoids

Several sibling examples in this repository are *named* "episodic" but do **not** configure the Episodic strategy:

| Example | What it's named | What it actually configures |
|---|---|---|
| `with-strands-agent/01-built-in-hook/meeting-notes-assistant-using-episodic` | "episodic" | **`SEMANTIC`** (`StrategyType.SEMANTIC.value`) |
| `with-strands-agent/03-memory-tool/debugging-agent` (`..._episodic_memory.py`) | "episodic" | **`semanticMemoryStrategy`** |
| `with-langgraph-agent/02-custom-callback/episodic-memory` | "episodic" | **`customMemoryStrategy`** with a `semanticOverride` |

Those configure semantic extraction (standalone facts) and relabel it "episodic." They do not produce episodes or cross-episode reflections from the dedicated pipeline. **This tutorial uses `episodicMemoryStrategy`** — the genuine strategy — and proves it.

### Verifying the strategy at runtime

Don't trust the request you sent — confirm what the service stored. The script calls `get_memory_strategies(memory_id)` after creation and asserts that a strategy's `type` contains `EPISODIC` (the control plane reports the type as the enum-style value `EPISODIC`). If you reuse an existing memory that was created with a different strategy, the script warns loudly instead of silently pretending it's episodic.

You can do the same from the CLI:

```bash
aws bedrock-agentcore-control get-memory --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --query 'memory.strategies[].type'
# Expect: [ "EPISODIC" ]
```

## Two decisions specific to this sample

How Episodic compares to the other three strategies, and what the Reflection step does, is covered
once in [`../../../../01-built-in-strategies/`](../../../../01-built-in-strategies/). Two choices
here are worth calling out because they are easy to get wrong:

### Session boundaries = episode boundaries

The most important implementation detail: **one session is one episode.** We give each debugging session a distinct `session_id` and write its turns with a **single `create_event`** call. Grouping a session's turns into one event hands the episodic pipeline a clean, self-contained sequence to consolidate into exactly one episode. Spreading one logical session across many tiny events, or mixing unrelated sessions under one `session_id`, muddies the episode boundaries.

### Namespace choice

This tutorial uses an **actor-level** namespace, `/episodes/{actorId}/`, rather than the SDK's session-scoped default (`/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/`). The reason is the use case: "what happened in my *previous* debugging sessions?" requires a single retrieval to span all of a developer's episodes. An actor-level namespace makes every session's episode retrievable together; a session-scoped namespace would isolate each episode to its own session and defeat cross-session recall.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model-invocation permissions. The `AnthropicBedrock` client and `MemoryClient` both resolve credentials from the standard AWS chain (environment variables, `~/.aws/credentials`, or an instance/role profile).
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region. Request it in the Bedrock console under *Model access*. (Model availability varies by region; `us-west-2` is a safe default.)
- No IAM execution role is required — built-in strategies (Episodic included) use AgentCore-managed models for extraction, consolidation, and reflection.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python claude-sdk-ltm-episodic.py
```

Expected output: two full debugging sessions (a memory leak, then API timeouts), each stored as an episode; a wait while the episodic pipeline runs; the extracted episodes printed back; then a "third session" for a new-but-similar memory leak — where the agent recalls the earlier unbounded-cache episode and applies the lesson, using only the episodic memory injected into the system prompt. The script then deletes the memory resource.

> **Note on timing:** the episodic pipeline is asynchronous and three-step (Extraction → Consolidation → Reflection), so it takes longer than single-step semantic extraction. The script polls for up to ~3 minutes; if episodes haven't surfaced by then it warns and continues (they typically appear shortly after, and reflections later still). Re-running retrieval a little later will pick them up. This is expected behavior, not an error.

## Key implementation notes

- **The strategy is what makes memory episodic.** The `create_event` call is identical to the semantic and short-term tutorials — attaching the `episodicMemoryStrategy` is the only difference, and it's what routes stored sessions through the episode pipeline.
- **Verify, don't assume.** The script checks `get_memory_strategies` for an `EPISODIC` type, so a misconfiguration (or a reused memory with the wrong strategy) is caught loudly.
- **One session = one episode.** Each session gets its own `session_id` and a single `create_event`, keeping episode boundaries clean.
- **Built-in strategies need no IAM role.** AgentCore manages the extraction/consolidation/reflection models. To swap in your own models or prompts, use the strategy-override examples under `02-long-term-memory` (Episodic override keys: `episodicExtractionOverride`, `episodicConsolidationOverride`, `episodicReflectionOverride`).
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept wildcards — substitute `{actorId}` yourself before calling.
- **The SDK is stateless, so memory is injected via the prompt.** Across sessions, a Claude agent "remembers" by retrieving episodes and folding them into the `system` prompt — there is no server-side session on the Anthropic side.
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a turn raises. Comment out the delete block to keep the memory between runs (and to let Reflection finish) — `get_or_create_memory` will reuse it by name.

## Where to go next

- The built-in Episodic strategy primitive and AWS CLI walkthrough: [`../../../../01-built-in-strategies/episodic.py`](../../../../01-built-in-strategies/episodic.py) and its [README](../../../../01-built-in-strategies/README.md)
- The long-term memory overview (all strategies, retrieval, namespaces): [`../../../../README.md`](../../../../README.md)
- The same Claude SDK patterns: [`../01-built-in-strategies/`](../01-built-in-strategies/) (Semantic), [`../02-custom-strategy-override/`](../02-custom-strategy-override/), [`../03-memory-as-tool/`](../03-memory-as-tool/)
- Episodic memory in a framework: [`../../with-strands-agent/`](../../with-strands-agent/), [`../../with-langgraph-agent/`](../../with-langgraph-agent/)
