# LlamaIndex + AgentCore memory — custom memory block (self-managed extraction)

A more sophisticated custom `BaseMemoryBlock` than
[`../01-built-in-memory-block/`](../01-built-in-memory-block/): this one **owns its
extraction logic**. It decides *what* is worth storing, distills it before writing, and
score-filters what it reads back — the "self-managed" philosophy applied **at the block
level**, in-process, with no extra infrastructure.

| Information | Details |
|---|---|
| Tutorial type | Long-term, single-agent |
| Agent usecase | Customer Support Assistant (durable customer profile across contacts) |
| Framework | LlamaIndex (`Memory` + custom `BaseMemoryBlock`, `FunctionAgent`) |
| LLM model | Claude Sonnet 4.6 — `global.anthropic.claude-sonnet-4-6` (via Amazon Bedrock) |
| Strategies | Semantic (facts) — **built-in**; extraction *gated* by the block |
| Memory components | Conditional `_aput` (LLM-judged), score-filtered `_aget`, client-side distillation |
| Complexity | Advanced |

> ### ⚠️ LlamaIndex API note — `Memory` + `BaseMemoryBlock` (NOT `ChatMemoryBuffer`)
> `ChatMemoryBuffer` is **deprecated**. This block subclasses `BaseMemoryBlock[str]` and
> implements `_aget` (retrieve, every turn) and `_aput` (persist, on short-term flush).
> Verified against `llama-index-core` 0.14.x.

## What makes this block "sophisticated"

1. **Conditional storage.** On flush, the block asks an in-process extractor LLM to judge
   whether the snippet contains a stable, reusable fact. Greetings and chit-chat are
   dropped; only durable facts are written.
2. **Client-side distillation (self-managed extraction).** It writes the *distilled* fact —
   one clean sentence — not the raw turns. You own the "what to extract" step.
3. **Score-filtered retrieval.** On read it keeps only records whose AgentCore relevance
   `score` clears `MIN_RELEVANCE_SCORE` (0.3), so weak matches never reach the LLM.
4. **Fail-safe parsing.** The extractor must return strict JSON; any unparseable output
   means *store nothing* rather than guess.

## 🛠️ "Self-managed" — two levels, and where this sits

AgentCore offers a **server-side** self-managed strategy (`customMemoryStrategy` +
`selfManagedConfiguration`): AgentCore drops conversation payloads to **your S3 bucket**,
notifies **your SNS topic**, and **your** Lambda writes records back via
`BatchCreateMemoryRecords`. That needs standing infrastructure and an execution role — it's
documented at [`../../../../03-self-managed-strategy/`](../../../../03-self-managed-strategy/).

This tutorial shows the **client-side / in-process** form: the LlamaIndex block runs the
extraction itself, synchronously, with zero extra infra. Use it when your extraction logic
is light enough to run in the agent process. Reach for the server-side strategy when
extraction is heavy, must use external context (CRM/EHR), or has to run out-of-band.

## Architecture

```
                         LlamaIndex FunctionAgent
                                   │
                    agent.run(msg, memory=Memory)
                                   │
              ┌────────────────────┴─────────────────────┐
              │             LlamaIndex Memory             │
              │   short-term FIFO (token-bounded queue)   │
              │                   │ flush (over budget)   │
              │                   ▼                       │
              │   SelfManagedMemoryBlock (BaseMemoryBlock)│
              │     _aput:  LLM distill ─▶ worth storing? ─┼─ no ─▶ drop
              │                              │ yes          │
              │     _aget:  search ─▶ score ≥ threshold?   │
              └───────────┬───────────────────┬───────────┘
                 search    │                   │  create_event
           (filter by score)                  (distilled fact only)
                          ▼                    ▼
            ┌─────────────────────────────────────────────┐
            │         AgentCore Memory (one memory_id)      │
            │   Semantic strategy →                         │
            │     /support/{actorId}/profile/               │  ◀── write AND read here
            └─────────────────────────────────────────────┘
```

## ✅ Namespace correctness

As in the built-in block, the strategy's write namespace and the block's read namespace are
the **same resolved template** — `/support/{actorId}/profile/`. This avoids the drift bug in
the older memory-as-tool tutorials (which searched a hard-coded `/strategies/` prefix that
didn't match the write target, so retrieval returned nothing). **Read from where you write.**

## What it does

[`llamaindex-ltm-custom-memory-block.py`](./llamaindex-ltm-custom-memory-block.py):

1. Creates (or reuses) one memory with a built-in Semantic strategy on
   `/support/{actorId}/profile/`.
2. **Session 1** mixes memory-worthy facts (plan, billing rules, recurring issue) with
   throwaway chit-chat. The block stores the former and drops the latter.
3. Waits ~90s for asynchronous extraction.
4. **Session 2** (fresh `Memory`) verifies recall, with only score-clearing facts surfacing.
5. Deletes the memory resource in a `finally` block.

## Prerequisites

- Python 3.10+
- AWS credentials with **both** AgentCore Memory permissions and Amazon Bedrock model access
- IAM permissions: `bedrock-agentcore:CreateMemory`, `:DeleteMemory`, `:GetMemory`,
  `:CreateEvent`, `:RetrieveMemoryRecords`
- Amazon Bedrock model access for **Claude Sonnet 4.6** in your region
- **No IAM execution role required** — the gating/distillation runs in-process.

## How to run

```bash
pip install -r requirements.txt
export AWS_REGION=us-west-2   # optional; defaults to us-west-2
python llamaindex-ltm-custom-memory-block.py
```

Expected output: Session 1 prints `🧠 Stored distilled fact` for the plan/billing/issue
turns and `🚫 Nothing memory-worthy … skipping write` for the chit-chat; Session 2 recalls
the stored facts (Enterprise + SSO, EUR invoices, the Monday export issue) and not the
weather/greeting talk.

## Key implementation notes

- **Two LLMs, distinct roles.** One drives the conversation; a second (`bind_extractor`)
  runs the distillation/gating step. They can be the same model id but are separate calls.
- **Fail-safe extraction.** Unparseable extractor output → store nothing. Memory writes
  never fabricate.
- **Score filtering uses the `score` field** AgentCore returns on each retrieved record
  (a Double, higher = more relevant; missing score is treated as 0.0 → conservative).
- **`priority=0`** keeps the profile block from being truncated out of context.
- **Cleanup runs in `finally`.** Comment out the delete block to keep the memory.

## Where to go next

- The simpler built-in block: [`../01-built-in-memory-block/`](../01-built-in-memory-block/)
- Memory-as-tool pattern: [`../03-memory-tool/`](../03-memory-tool/)
- Full server-side self-managed strategy (SNS + S3 + Lambda):
  [`../../../../03-self-managed-strategy/`](../../../../03-self-managed-strategy/)
- Long-term memory overview: [`../../../../README.md`](../../../../README.md)
