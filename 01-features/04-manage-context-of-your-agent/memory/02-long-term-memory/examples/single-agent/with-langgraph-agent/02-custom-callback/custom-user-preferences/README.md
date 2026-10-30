# Long-term memory — LangGraph custom user-preference callback

A nutrition assistant built with **LangGraph** that uses a **custom-override UserPreference strategy** plus pre/post model hooks to automatically extract, store, and recall user preferences across sessions. Custom prompts steer how preferences are extracted and consolidated.

| Information | Details |
|---|---|
| Tutorial type | Long-term conversational |
| Agent type | Nutrition Assistant |
| Framework | LangGraph (`create_react_agent` + pre/post model hooks) |
| LLM model | Anthropic Claude Haiku 4.5 |
| Strategies | UserPreference — **custom override** (requires IAM execution role) |
| Memory components | `AgentCoreMemoryStore` (long-term), custom extraction/consolidation prompts, pre/post model hooks, semantic retrieval |
| Checkpointer | `AgentCoreMemorySaver` — same `memory_id` as the store |
| Complexity | Intermediate |

> **Which integration does what.** The script wires **both** classes against one `memory_id`:
> `store=AgentCoreMemoryStore(...)` for the preferences (*this user*) and
> `checkpointer=AgentCoreMemorySaver(...)` so the conversation itself (*this conversation*)
> survives the process exiting. See
> [both classes, one memory resource](../../README.md#both-classes-one-memory-resource).

## What it does

[`nutrition-assistant-with-user-preference-saving.py`](./nutrition-assistant-with-user-preference-saving.py):

1. Creates memory with a UserPreference custom-override strategy (namespace `/{actorId}/preferences/`), using the prompts in [`custom_memory_prompts.py`](./custom_memory_prompts.py).
2. A **pre-model hook** writes the user turn via `store.put((actor_id, thread_id), ...)` and retrieves relevant preferences with `store.search((actor_id, "preferences/"), ...)`, injecting them into context.
3. A **post-model hook** stores the assistant turn for asynchronous extraction.
4. Across sessions (same `actor_id`, new `thread_id`), the agent recalls dietary restrictions, favorite foods, and health goals to personalize advice — that proves the `store`.
5. Sends one more turn on that same second `thread_id` and asks *"what did I just ask you?"* — that proves the `checkpointer`, since only the saved graph state can answer it.

Session ids are suffixed with a per-run `uuid` so each run starts a genuinely new conversation.
With a durable checkpointer and a hardcoded session id, re-running would *resume* the previous
run instead, and the "the model doesn't know your preferences yet" narrative would break.

## Prerequisites

- Python 3.10+
- AWS account with AgentCore Memory permissions and an IAM execution role
- Access to Amazon Bedrock models

## How to run

```bash
pip install -r requirements.txt
python nutrition-assistant-with-user-preference-saving.py
```

See the sibling [`episodic-memory/`](../episodic-memory/) for the episodic variant, or the [LangGraph single-agent README](../../README.md) for all three patterns.
