# Long-term memory — Claude SDK multi-agent (shared memory)

A **multi-agent research team** built directly on the **Anthropic Claude SDK** (via Amazon Bedrock), with **no agent framework**, where several specialized agents collaborate through a **single shared AgentCore Memory resource**.

The single-agent Claude SDK tutorials ([`../../single-agent/with-claude-sdk/`](../../single-agent/with-claude-sdk/)) wired memory into one agent. This one turns the same memory primitive into the **communication channel** for a team: one agent writes, the next reads what it wrote, and so on down a pipeline. Because the Anthropic SDK is a stateless API client — no shared session, no framework passing state between agents — the multi-agent mechanics are fully explicit: the only thing the agents have in common is a `memory_id`.

| Information | Details |
|---|---|
| Tutorial type | Long-term, multi-agent |
| Agent type | Research team — Researcher → Analyst → Report Writer |
| Framework | Anthropic Claude SDK (no framework) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategy | Semantic — **built-in** (`semanticMemoryStrategy`, no IAM execution role) |
| Memory components | `create_memory_and_wait` (Semantic strategy), `create_event`, `retrieve_memories`, `list_memories`, `delete_memory_and_wait` |
| Complexity | Advanced |

## What it does

[`claude-sdk-multi-agent-shared-memory.py`](./claude-sdk-multi-agent-shared-memory.py):

1. Creates (or reuses) **one** memory resource with a built-in **Semantic strategy** — no IAM execution role required.
2. Runs three agents as **separate Claude conversations** (each with its own `messages[]` array and its own `actor_id`), sharing the one `memory_id`:
   - **Research Agent** gathers findings on the topic and writes them to the **shared blackboard**.
   - **Analyst Agent** retrieves the researcher's findings *from shared memory*, synthesizes them, and writes the synthesis back.
   - **Report Agent** retrieves **both** the findings and the synthesis, then writes the final executive summary.
3. Between stages, **waits for the asynchronous extraction pipeline** so the next agent can retrieve the previous agent's output (the producer/consumer handoff).
4. Prints what lives in **shared** memory (the whole team's output) versus what lives in one agent's **private** slice.
5. Deletes the memory resource in a `finally` block.

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
    │  own messages[]  │ │  own messages[]  │ │  own messages[]  │
    │  Claude/Bedrock  │ │  Claude/Bedrock  │ │  Claude/Bedrock  │
    └──────────────────┘ └──────────────────┘ └──────────────────┘
         1. gather            2. synthesize         3. summarize
         findings ──▶ memory  read findings ──▶     read findings +
                              write synthesis       synthesis ──▶ report

  Sequential handoff:  Research ─(wait for extraction)─▶ Analyst ─(wait)─▶ Report
```

## How memory is shared (the core idea)

A memory resource is just a `memory_id` — nothing binds it to a single agent. Every agent in this script targets the **same** `memory_id`; that is what makes the memory *shared*. Three identifiers decide who-sees-what:

| Identifier | Role in a multi-agent system |
|---|---|
| `memory_id` | The shared resource. **Same** for every agent — this is what makes memory shared. |
| `actor_id` | **Who** is writing/reading. Each agent has its own, plus one team identity. |
| `namespace` | **Where** records land. `{actorId}` is substituted from the event's `actor_id`. |

The single namespace template `"/research-team/{actorId}/knowledge/"` produces a **shared pool** or a **private slice** depending only on which `actor_id` you write under — verified against this repo's [`../../../04-namespaces/`](../../../04-namespaces/) tutorial, where `{actorId}` is filled in from the `actorId` on `CreateEvent`:

| Write with `actor_id` = | Resolves to | Meaning |
|---|---|---|
| `"team-shared"` | `/research-team/team-shared/knowledge/` | **Team blackboard** — every agent reads & writes it |
| `"research-agent"` | `/research-team/research-agent/knowledge/` | Researcher's **private** slice |
| `"analyst-agent"` | `/research-team/analyst-agent/knowledge/` | Analyst's **private** slice |

**Agent B sees Agent A's work because B retrieves from the shared blackboard that A wrote to** — not because they share a conversation (they don't). That is the producer/consumer pattern with AgentCore Memory as the channel. One Semantic strategy with one namespace template covers both the shared pool and every private slice; you do **not** need a strategy per agent.

### Why one strategy, one template

`{actorId}` is the only lever you need. Because it is substituted at extraction time from whatever `actor_id` you pass to `create_event`, the same strategy fans every agent's writes into the right place: shared writes go to the `team-shared` actor's namespace, private writes go to each agent's own. Retrieval is by **exact resolved namespace** (the installed SDK's `retrieve_memories` does not accept wildcards), so reading "the shared blackboard" means resolving the template with `actorId="team-shared"`.

## The multi-agent lifecycle

| Step | Where | How |
|---|---|---|
| **Create** | Once, at startup | `create_memory_and_wait(name=..., strategies=[{semanticMemoryStrategy: {namespaceTemplates: ["/research-team/{actorId}/knowledge/"]}}])` |
| **Produce** | Each agent, after it answers | `create_event(memory_id, actor_id="team-shared", session_id, messages=[(output, "USER")])` — publish to the blackboard |
| **Wait** | Between stages | Poll `retrieve_memories` on the shared namespace until the producer's records surface (extraction is asynchronous) |
| **Consume** | Next agent, before it answers | `retrieve_memories(memory_id, namespace="/research-team/team-shared/knowledge/", query=..., top_k=...)` and fold the results into the agent's user turn |
| **Inspect** | End of run | Retrieve from the shared namespace vs an agent's private namespace to see shared-vs-isolated |

Each agent is a **separate Claude conversation**: a fresh `messages[]` array, its own system prompt, its own `actor_id`. Nothing is carried between agents except what passes *through* memory.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model-invocation permissions. The `AnthropicBedrock` client and `MemoryClient` both resolve credentials from the standard AWS chain (environment variables, `~/.aws/credentials`, or an instance/role profile).
- **Amazon Bedrock model access for Claude Sonnet 4.6** in your region (request it in the Bedrock console under *Model access*; `us-west-2` is a safe default).
- No IAM execution role is required — built-in strategies (Semantic included) use AgentCore-managed models for extraction and consolidation.

## How to run

```bash
pip install -r requirements.txt

# Optional: override the region (defaults to us-west-2)
export AWS_REGION=us-west-2

python claude-sdk-multi-agent-shared-memory.py
```

Expected output: the Research Agent's findings, a wait while extraction runs, the Analyst Agent's synthesis (built from the researcher's findings it retrieved from shared memory), another wait, the Report Agent's executive summary (built from both), then a print of what lives in the shared blackboard versus one agent's private slice. The script then deletes the memory resource.

> **Note on timing:** long-term extraction is asynchronous, so the script waits (up to ~2 minutes per stage) for each agent's output to surface before handing off. If records haven't surfaced in time it warns and continues with whatever is available; the downstream agent simply has less context. This is expected behavior, not an error. In production you would not block a pipeline between every stage — you would let producers run ahead and have consumers pick up work later.

## Key implementation notes

- **Shared `memory_id` is the whole trick.** Every agent constructs its work against the same memory resource. There is no special "multi-agent" API — collaboration is just multiple clients reading and writing one resource.
- **`actor_id` selects shared vs private.** The same namespace template resolves to the team blackboard (`actor_id="team-shared"`) or an agent's private slice (`actor_id=<agent>`). This is the single lever for isolation vs sharing.
- **Producer/consumer over memory, not over a conversation.** Agents never share a `messages[]` array. Agent B reads Agent A's output by retrieving it from the shared namespace — the only channel between them.
- **Sequential handoff waits for extraction.** Because each stage consumes the previous stage's output, the script polls until the producer's records are extracted before the consumer retrieves. Single-agent tutorials only wait once; a pipeline waits between stages.
- **Retrieval namespaces must be fully resolved.** `retrieve_memories` does not accept wildcards — the template is resolved with the target `actor_id` before each call.
- **Built-in strategy needs no IAM role.** AgentCore manages the extraction/consolidation models. To customize them, see the strategy-override examples under [`../../../02-strategy-overrides/`](../../../02-strategy-overrides/).
- **The SDK is stateless, so context is injected via the prompt.** Retrieved records are folded into each agent's user turn — there is no server-side session on the Anthropic side.
- **Cleanup runs in `finally`.** The shared memory resource is billable, so it's deleted even if a stage raises. Comment out the delete block to keep the memory between runs — `get_or_create_memory` will reuse it by name.

## Where to go next

- Namespaces, shared vs isolated record sets, and retrieval modes: [`../../../04-namespaces/`](../../../04-namespaces/)
- The single-agent Claude SDK patterns this builds on: [`../../single-agent/with-claude-sdk/`](../../single-agent/with-claude-sdk/)
- The same multi-agent idea in a framework: [`../with-strands-agent/`](../with-strands-agent/)
- Short-term multi-agent (shared session, branching): [`../../../../01-short-term-memory/examples/multi-agent/`](../../../../01-short-term-memory/examples/multi-agent/)
