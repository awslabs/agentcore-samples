# Usage patterns: wiring memory into an agent

The quickstarts call the memory APIs directly, but you don't need to do that in your applications. This page is the single
reference for **who calls the API in your agent** — the framework's own persistence slot, a
lifecycle hook you write, or a tool the model invokes — and what each framework calls it.

## Two questions, two mechanisms

Memory answers two different questions, and they are wired separately in every framework:

|                       | Short-term                                     | Long-term                                                                                 |
| --------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Question**          | "Resume _this conversation_ where it left off" | "What do I know about _this user_ from other conversations?"                              |
| **Stores**            | Raw conversation turns / graph state, as-is    | Facts a strategy extracted from those turns                                               |
| **Read latency**      | Immediate                                      | Extraction is asynchronous — a fact written this turn is usually not searchable next turn |
| **Needs a strategy?** | No                                             | Yes                                                                                       |

**Neither is optional in production.** Short-term with no strategy forgets the user between
conversations; long-term with no conversation persistence forgets the conversation you are
currently in. Most production agents wire both.

## Who drives the calls?

| Pattern                                                  | Who decides when to save/recall                                      | When to use                                                                                     |
| -------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Adapter** (session manager / checkpointer / memory)    | The framework, automatically                                         | Default for short-term. Zero memory code in your agent                                          |
| **Built-in hook / callback / middleware / memory-block** | The framework's lifecycle, on a standard save-then-retrieve schedule | Default for long-term. Fastest path                                                             |
| **Custom hook**                                          | You, in code, at a lifecycle point you pick                          | Conditional/ more optimal writes for your usecase, custom queries, multi-strategy orchestration |
| **Memory-as-tool**                                       | The model, via a tool call                                           | The agent should choose when to remember or look something up                                   |

The first is short-term's answer; the last three are long-term's. They compose: a production
agent typically has an adapter for the conversation _and_ a hook or tool for the facts.

## Pattern 1 — the adapter (short-term)

Every framework has a class that persists the conversation for you, so you never call
`create_event` / `list_events` by hand. Same job, four different names:

| Framework          | Class → argument                                                                           | Import from                                                                                     | pip package                            |
| ------------------ | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- | -------------------------------------- |
| **Strands Agents** | `AgentCoreMemorySessionManager` (+ `AgentCoreMemoryConfig`) → `Agent(session_manager=...)` | `bedrock_agentcore.memory.integrations.strands.session_manager` (config in `...strands.config`) | `bedrock-agentcore`                    |
| **LangGraph**      | `AgentCoreMemorySaver`, a _checkpointer_ → `create_agent(checkpointer=...)`                | `langgraph_checkpoint_aws`                                                                      | `langgraph-checkpoint-aws`             |
| **LlamaIndex**     | `AgentCoreMemory` (+ `AgentCoreMemoryContext`) → `FunctionAgent(memory=...)`               | `llama_index.memory.bedrock_agentcore`                                                          | `llama-index-memory-bedrock-agentcore` |
| **No framework**   | `MemorySessionManager` or `MemoryClient` — you call them yourself                          | `bedrock_agentcore.memory`                                                                      | `bedrock-agentcore`                    |

Only the Strands adapter ships in the AgentCore SDK. LangGraph and LlamaIndex users install a separate package.

The identity contract is the same everywhere: an **actor id** (who) and a **session id**
(which conversation). Only the argument names change — LangGraph calls the session id
`thread_id`, in `configurable`.

**Strands**

```python
from strands import Agent
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

config = AgentCoreMemoryConfig(memory_id=memory_id, actor_id="user-1", session_id="session-1")
agent = Agent(session_manager=AgentCoreMemorySessionManager(config, region_name=region))
```

**LangGraph**

```python
from langgraph_checkpoint_aws import AgentCoreMemorySaver

agent = create_agent(model, tools, checkpointer=AgentCoreMemorySaver(memory_id, region_name=region))

# Both ids are required on every invoke; thread_id → sessionId, actor_id → actorId.
agent.invoke(
    {"messages": [...]},
    {"configurable": {"thread_id": "session-1", "actor_id": "user-1"}},
)
```

**LlamaIndex**

```python
from llama_index.memory.bedrock_agentcore import AgentCoreMemory, AgentCoreMemoryContext

context = AgentCoreMemoryContext(memory_id=memory_id, actor_id="user-1", session_id="session-1")
agent_memory = AgentCoreMemory(context=context, region_name=region)
response = await agent.run(user_msg, memory=agent_memory)
```

> **Two similarly named classes.** `MemorySessionManager` is the AgentCore **SDK** helper and
> works with any framework or none. `AgentCoreMemorySessionManager` is the **Strands** adapter
> that wraps it. Not using Strands? You want the first one.

## Pattern 2 — a hook, callback, or middleware (long-term)

The adapter persists the conversation; a hook is where you write turns for extraction and read
extracted facts back. Each framework exposes a different lifecycle seam:

| Framework              | Seam                                                               | Fires                                          |
| ---------------------- | ------------------------------------------------------------------ | ---------------------------------------------- |
| **Strands**            | `HookProvider` — `AgentInitializedEvent`, `MessageAddedEvent`      | On agent start / on each message               |
| **Strands (built-in)** | `AgentCoreMemoryConfig(retrieval_config={...})` — no hook to write | The session manager retrieves before each turn |
| **LangGraph v1.0**     | `@dynamic_prompt`, `@before_model`, `@after_model` middleware      | Around the model call                          |
| **LlamaIndex**         | `BaseMemoryBlock` subclass — `aput` / `aget`                       | On write / on context assembly                 |

**Strands — retrieval with no hook at all.** The session manager can do long-term retrieval
itself; give it a namespace and it injects matching records into context each turn:

```python
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)

config = AgentCoreMemoryConfig(
    memory_id=memory_id,
    actor_id=actor_id,
    session_id=session_id,
    retrieval_config={f"/users/{actor_id}/facts/": RetrievalConfig(top_k=5, relevance_score=0.4)},
)
```

**Strands — a custom hook** when you need conditional logic instead:

```python
from strands.hooks import HookProvider, HookRegistry, AgentInitializedEvent, MessageAddedEvent

class MemoryHookProvider(HookProvider):
    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)  # recall
        registry.add_callback(MessageAddedEvent, self.on_message_added)          # save
```

**LangGraph — v1.0 middleware** over an `AgentCoreMemoryStore`:

```python
from langchain.agents import create_agent
from langchain.agents.middleware import after_model, dynamic_prompt
from langgraph_checkpoint_aws import AgentCoreMemorySaver, AgentCoreMemoryStore

@dynamic_prompt          # recall: search the store, prepend the facts to the system prompt
def personalize(request): ...

@after_model             # save: store.put() the new turn for extraction
def persist(state, runtime): ...

agent = create_agent(
    model, tools,
    store=AgentCoreMemoryStore(memory_id=memory_id, region_name=region),   # this user
    checkpointer=AgentCoreMemorySaver(memory_id, region_name=region),      # this conversation
    middleware=[personalize, persist],
)
```

**LlamaIndex — a memory block**, which is the same idea as a hook, expressed as a class:

```python
from llama_index.core.memory import Memory, BaseMemoryBlock

class AgentCoreMemoryBlock(BaseMemoryBlock[str]):
    async def _aput(self, messages): ...   # write turns for extraction
    async def _aget(self, messages, **kw): ...  # retrieve_memories, injected into context

memory = Memory.from_defaults(session_id=session_id, memory_blocks=[AgentCoreMemoryBlock(...)])
```

## Pattern 3 — memory as a tool (long-term)

Give the model `store_memory` / `recall_memory` tools and let it decide. Use this when recall
is occasional and query-dependent rather than needed on every turn — the trade-off is that the
model may not call the tool when it should.

**Strands** ships a ready-made provider:

```python
from strands import Agent
from strands_tools.agent_core_memory import AgentCoreMemoryToolProvider

provider = AgentCoreMemoryToolProvider(
    memory_id=memory_id, actor_id=actor_id, session_id=session_id, namespace=namespace
)
agent = Agent(tools=provider.tools)
```

**LangGraph** — a plain `@tool` over `MemoryClient`:

```python
from langchain_core.tools import tool
from bedrock_agentcore.memory import MemoryClient

client = MemoryClient(region_name=region)

@tool
def recall_memory(query: str) -> str:
    """Search what we know about this user."""
    records = client.retrieve_memories(memory_id=memory_id, namespace=namespace, query=query, top_k=5)
    return "\n".join(r["content"]["text"] for r in records)

agent = create_agent(model, tools=[recall_memory], checkpointer=checkpointer)
```

## LangGraph specifically: checkpointer vs. store

`langgraph-checkpoint-aws` gives you **two** classes and the names are close enough to be
confusing. The one thing to remember:

> **The checkpointer resumes _this conversation_. The store recalls facts about _this user_.**

They are not alternatives — they fill two different arguments and can point at the same memory
resource:

```python
agent = create_agent(
    model, tools,
    checkpointer=AgentCoreMemorySaver(memory_id),        # memory_id is positional here
    store=AgentCoreMemoryStore(memory_id=memory_id),     # ...and keyword-only here
)
```

|                       | `AgentCoreMemorySaver` (`checkpointer=`)                                           | `AgentCoreMemoryStore` (`store=`)                          |
| --------------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| **Use it for**        | Short-term memory                                                                  | Long-term memory                                           |
| **Saves**             | The whole graph state — every message, tool call, and paused `interrupt()` — as-is | One message at a time, for AgentCore to extract facts from |
| **Payload written**   | `blob` — opaque, no strategy ever reads it                                         | `conversational` — this is what gets extracted             |
| **Reads back**        | Automatically, on the next `invoke` with the same `thread_id`                      | When you call `store.search(...)`                          |
| **Needs a strategy?** | No                                                                                 | Yes                                                        |

Because the payload types differ, **both can share one `memory_id`** — checkpoint data never
pollutes your extracted records, and you don't need a strategy for the checkpointer.

Three things that trip people up:

- **The store is not a faster checkpointer.** Extraction is asynchronous. For continuity
  _within_ one conversation you need the checkpointer.
- **`InMemorySaver` is not a drop-in swap.** `AgentCoreMemorySaver` requires **both**
  `thread_id` _and_ `actor_id` in `configurable` and raises `InvalidConfigError` without them;
  `InMemorySaver` only needs `thread_id`.
- **Namespaces differ per call, and the strategy sets the recall scope.**
  `store.put` takes exactly `(actor_id, session_id)` — so the store *writes* one conversation
  at a time, same as the checkpointer. `store.search` instead takes the _strategy's_ namespace,
  and because `AgentCoreMemoryStore` defaults to `hierarchical_search=True` it sends that path
  as `namespacePath`, which matches every record whose namespace **starts with** it. So a
  session-scoped template is not the same as session-scoped memory — the search decides:

  ```python
  # strategy namespace: /nutrition/{actorId}/{sessionId}/
  store.search(("nutrition", actor_id, ""),         query=q)  # /nutrition/u-1/      → all sessions
  store.search(("nutrition", actor_id, sess + "/"), query=q)  # /nutrition/u-1/s-3/  → one session
  ```

  Keep the trailing slash: matching is on the string, so `/nutrition/user-1` also matches actor
  `user-10`. Pass `hierarchical_search=False` for exact-path matching instead. The same split
  exists one layer down on `MemoryClient.retrieve_memories`: `namespace=` is exact,
  `namespace_path=` is the prefix.

Source: [`langgraph-checkpoint-aws`](https://github.com/langchain-ai/langchain-aws/tree/main/libs/langgraph-checkpoint-aws)
in the [`langchain-aws`](https://github.com/langchain-ai/langchain-aws) repo.

## Worked examples

|                  | Short-term (adapter)                                                                                                                                                                                        | Long-term (hook / tool)                                                                         |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| **Strands**      | [`with-strands-agent/`](../01-short-term-memory/examples/single-agent/with-strands-agent/) (hooks, to show the mechanism) · [runtime integration](../03-integrations/01-runtime-integration/) (the adapter) | [`with-strands-agent/`](../02-long-term-memory/examples/single-agent/with-strands-agent/)       |
| **LangGraph**    | [`with-langgraph-agent/`](../01-short-term-memory/examples/single-agent/with-langgraph-agent/)                                                                                                              | [`with-langgraph-agent/`](../02-long-term-memory/examples/single-agent/with-langgraph-agent/)   |
| **LlamaIndex**   | [`with-llamaindex-agent/`](../01-short-term-memory/examples/single-agent/with-llamaindex-agent/)                                                                                                            | [`with-llamaindex-agent/`](../02-long-term-memory/examples/single-agent/with-llamaindex-agent/) |
| **No framework** | [`with-claude-sdk/`](../01-short-term-memory/examples/single-agent/with-claude-sdk/)                                                                                                                        | [`with-claude-sdk/`](../02-long-term-memory/examples/single-agent/with-claude-sdk/)             |

## Before you ship

Conversation persistence is the most common way a "memory-enabled" agent still forgets — an
in-process saver loses every conversation on restart and shares nothing between replicas. See
[`06-production-patterns/03-production-checklist.md`](../06-production-patterns/03-production-checklist.md#6-conversation-persistence-session-manager--checkpointer).
