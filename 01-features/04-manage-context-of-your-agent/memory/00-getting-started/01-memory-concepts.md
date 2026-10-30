# AgentCore Memory concepts

AgentCore Memory is built around a small set of primitives. Every tutorial in this repo assumes you know these six terms.

## The six primitives

| Term                | What it is                                                                                                                                                                           | Scope            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| **Memory resource** | The top-level container. Holds events, strategies, and extracted records. Has an ID, IAM execution role, optional CMK, event expiry.                                                 | Account / region |
| **Actor**           | Who is producing events — typically a user, but can be an agent or any stable principal. An `actorId` is any string you choose.                                                      | Memory resource  |
| **Session**         | A bounded conversation or interaction. A `sessionId` groups events that share context (e.g., a single chat).                                                                         | Actor            |
| **Event**           | A single write to short-term memory. Carries a `payload` list whose items are `conversational` turns, `json` documents, or `blob` data, plus optional metadata and a `branchId`.     | Session          |
| **Strategy**        | A rule for extracting long-term memory records from events. Built-in (Semantic, Summary, User Preference, Episodic), built-in with prompt overrides, or self-managed (your Lambdas). | Memory resource  |
| **Memory record**   | A structured fact, preference, summary, or episode produced by a strategy. Retrievable by semantic search.                                                                           | Namespace        |

An event is not only for conversation. `conversational` and `json` payload items are both extracted into long-term memory; `blob` items stay in short-term memory. See [`../01-short-term-memory/05-payload-types/`](../01-short-term-memory/05-payload-types/)

## How they fit together

```
Memory resource
├── Events                 ← short-term memory (raw turns)
│   ├── Session A (Actor X)
│   │   ├── Event 1
│   │   ├── Event 2  ──── branchId "what-if" ──► Event 2a, 2b  (branching)
│   │   └── Event 3
│   └── Session B (Actor Y)
└── Memory records         ← long-term memory (extracted by strategies)
    ├── Namespace /users/X/semantic/...
    ├── Namespace /users/X/preferences/...
    └── Namespace /episodes/...
```

## Namespaces

A **namespace** organizes long-term records into a hierarchical path. Templates like `{actorId}`, `{sessionId}`, and `{memoryStrategyId}` expand at write time:

- `/users/{actorId}/facts/` → `/users/user-42/facts/`
- `/sessions/{sessionId}/summary/` → `/sessions/sess-9/summary/`

Namespaces are the primary axis for IAM scoping, tenant isolation, and targeted retrieval.

## Short-term vs long-term

| | Short-term | Long-term |
|---|---|---|
| Unit | Event | Memory record |
| Created by | You (`CreateEvent`) — or your framework's adapter, on your behalf | A strategy (built-in or self-managed) |
| Organized by | Session + actor | Namespace |
| Retrieved via | `ListEvents`, `GetEvent`, `get_last_k_turns` | `RetrieveMemoryRecords` (semantic search), `ListMemoryRecords` |
| Typical retention | Days to a year (`event_expiry_days`) | As long as the memory resource lives |

> **In agent code you usually don't call these APIs yourself.** Every major framework has an
> AgentCore adapter — one per column above — that writes the events and retrieves the records
> for you. Same primitives underneath; you just don't hand-roll the plumbing. See
> [06-usage-patterns.md](./06-usage-patterns.md).

## Next

- Pick a surface in [02-choosing-your-surface.md](./02-choosing-your-surface.md).
- Then walk the end-to-end quickstart in your preferred surface.
