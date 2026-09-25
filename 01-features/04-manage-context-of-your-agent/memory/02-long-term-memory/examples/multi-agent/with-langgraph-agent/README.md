# Long-term memory — LangGraph, multi-agent shared memory

A **multi-agent** system built with **LangGraph** (`create_agent`) where several
specialized agents collaborate through a **single shared AgentCore Memory resource**. One
agent writes, the next reads what it wrote, and so on down a pipeline — AgentCore Memory is
the communication substrate.

The use case is a **research team**: a **Research Agent** gathers findings, an **Analyst
Agent** synthesizes them, and a **Report Agent** writes the executive summary. Each is a
separate LangGraph `create_agent` with its own `actor_id`; they share one `memory_id`.

| Information | Details |
|---|---|
| Tutorial type | Long-term, multi-agent |
| Agent usecase | Research team (Researcher → Analyst → Report Writer) |
| Framework | LangGraph (v1.0 `create_agent`) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in** (no IAM execution role) |
| Memory components | One shared `memory_id`, per-agent `actor_id`, shared vs private namespaces, `create_event`, `retrieve_memories` |
| Complexity | Advanced |

> ### ⚠️ LangGraph v1.0 API note
> Each agent is built with the current **`from langchain.agents import create_agent`** API
> (the older `langgraph.prebuilt.create_react_agent` is deprecated in v1.0). The agents
> have no tools — they reason over text — so the multi-agent coordination lives entirely in
> how the orchestrator routes each agent's output and input through the shared
> `MemoryClient`, keeping the shared-memory pattern explicit and framework-neutral.

## What it does

[`langgraph-multi-agent-shared-memory.py`](./langgraph-multi-agent-shared-memory.py):

1. Creates (or reuses) **one** memory resource with a built-in **Semantic** strategy whose
   namespace template is `/research-team/{actorId}/knowledge/`. No IAM execution role.
2. **Stage 1 — Research Agent**: gathers findings on the topic, writes them to the SHARED
   blackboard (`actor_id="team-shared"`) and a PRIVATE copy under its own actor_id.
3. *(waits for extraction)*
4. **Stage 2 — Analyst Agent**: retrieves the findings from the shared blackboard,
   synthesizes them, writes the synthesis back to the shared blackboard.
5. *(waits for extraction)*
6. **Stage 3 — Report Agent**: retrieves BOTH findings and synthesis from the shared
   blackboard and writes the final executive summary.
7. Inspects what lives in shared vs. private memory, then deletes the resource in a
   `finally` block.

## How memory is shared (the core idea)

A memory resource is identified by a plain `memory_id`. Three identifiers decide
who-can-see-what:

| Identifier | Role in a multi-agent system |
|---|---|
| `memory_id` | The shared resource. **SAME** for every agent — this is what makes memory shared. |
| `actor_id` | **WHO** is writing/reading. Each agent has a distinct one, plus a `team-shared` identity. |
| `namespace` | **WHERE** records land. `{actorId}` is substituted from the event's `actor_id`. |

The template `"/research-team/{actorId}/knowledge/"` yields a **shared** pool or a
**private** slice depending only on which `actor_id` you write under:

- Write with `actor_id="team-shared"` → `/research-team/team-shared/knowledge/` — the
  **team blackboard** every agent reads from and writes to.
- Write with `actor_id="research-agent"` → `/research-team/research-agent/knowledge/` —
  that agent's **private scratchpad**.

Agent B "sees" Agent A's work because B retrieves from the shared blackboard that A wrote
to — not because they share a conversation (they don't). This is the producer/consumer
pattern, with AgentCore Memory as the channel.

## Architecture

```
           ┌───────────────────────────────────────────────────────────────┐
           │           ONE shared AgentCore Memory  (single memory_id)       │
           │                                                                 │
           │   SHARED namespace  (actor_id = "team-shared")                  │
           │     /research-team/team-shared/knowledge/   ◀── team blackboard │
           │                                                                 │
           │   PRIVATE namespaces (actor_id = each agent)                    │
           │     /research-team/research-agent/knowledge/                    │
           │     /research-team/analyst-agent/knowledge/                     │
           └───────────────────────────────────────────────────────────────┘
                ▲ write          ▲ read+write          ▲ read
                │ shared         │ shared              │ shared
    ┌───────────┴──────┐ ┌───────┴──────────┐ ┌────────┴─────────┐
    │  Research Agent  │ │  Analyst Agent   │ │  Report Agent    │
    │  actor: research │ │  actor: analyst  │ │  actor: report   │
    │  create_agent    │ │  create_agent    │ │  create_agent    │
    │  (LangGraph)     │ │  (LangGraph)     │ │  (LangGraph)     │
    └──────────────────┘ └──────────────────┘ └──────────────────┘
         1. gather            2. synthesize         3. summarize
         findings ──▶ memory  read findings ──▶     read findings +
                              write synthesis       synthesis ──▶ report

  Sequential handoff:  Research ─(wait for extraction)─▶ Analyst ─(wait)─▶ Report
```

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

python langgraph-multi-agent-shared-memory.py
```

Expected output: each stage prints its agent's output and a `🧠 stored to memory` line;
between stages you'll see `⏳ Waiting for extraction` then `✅ Records are available`.
Finally the script prints what lives in the shared blackboard vs. one agent's private slice,
then deletes the memory resource.

> **Note on timing:** extraction is asynchronous, and this is a **sequential** pipeline —
> each agent consumes the previous one's output — so the script waits for records to
> surface between stages. Each wait is capped at ~2 minutes; if records haven't appeared it
> warns and continues (the next agent simply sees fewer records). In production you would
> not block between every stage.

## Key implementation notes

- **Shared `memory_id`, per-agent `actor_id`.** One resource, many actors. The `actor_id`
  on each `create_event` decides whether the record lands in the shared blackboard or an
  agent's private slice — there is no separate strategy per agent.
- **Agents never share a conversation.** Each `create_agent` invocation is standalone;
  everything that crosses between agents goes *through memory*. The orchestrator retrieves
  the upstream output and folds it into the next agent's user turn.
- **No checkpointer, deliberately.** None of the three agents is compiled with
  `checkpointer=`, because each one runs exactly once and needs no multi-turn continuity —
  the handoff is the shared namespace, not shared conversation state. If an agent in your
  pipeline *does* hold a multi-turn conversation (a user-facing front-end agent, say), give
  that agent an `AgentCoreMemorySaver` with its own `thread_id`; the pipeline agents can stay
  stateless. See
  [checkpointer vs. store](../../../../00-getting-started/06-usage-patterns.md#langgraph-specifically-checkpointer-vs-store).
- **Sequential handoff needs a wait.** Because extraction is asynchronous and stage N+1
  reads stage N's output, `wait_for_records` polls the shared namespace before handing off.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept
  wildcards — helpers substitute `{actorId}` before calling.
- **Thin wrappers keep the pattern visible.** `store_to_memory` / `retrieve_from_memory`
  are deliberately minimal wrappers over `create_event` / `retrieve_memories` so the
  multi-agent mechanics aren't hidden behind framework abstractions.
- **Cleanup runs in `finally`.** The memory resource is billable, so it's deleted even if a
  stage raises. Comment out the delete block to keep it between runs.

## Where to go next

- The single-agent LangGraph patterns:
  [`../../single-agent/with-langgraph-agent/`](../../single-agent/with-langgraph-agent/)
- The same multi-agent pattern with the Claude SDK:
  [`../with-claude-sdk/`](../with-claude-sdk/)
- The Strands multi-agent example: [`../with-strands-agent/`](../with-strands-agent/)
- The long-term memory overview (all strategies, retrieval, namespaces):
  [`../../../README.md`](../../../README.md)
