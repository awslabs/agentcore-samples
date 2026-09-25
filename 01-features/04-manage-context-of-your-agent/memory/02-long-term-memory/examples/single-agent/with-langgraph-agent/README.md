# Long-term memory — LangGraph single-agent

Three integration patterns for giving a LangGraph agent long-term memory backed by
Amazon Bedrock AgentCore Memory. They differ in **who decides when to store and recall**.

| Pattern | Folder | What it shows |
|---|---|---|
| **Built-in callback** — the framework manages the memory lifecycle | [`01-built-in-callback/`](./01-built-in-callback/) | `AgentCoreMemorySaver` (checkpointer) + `AgentCoreMemoryStore` (LTM store) wired via tiny `@dynamic_prompt` / `@after_model` middleware. The lowest-effort integration. |
| **Custom callback** — you hand-roll the hooks and the strategy | [`02-custom-callback/`](./02-custom-callback/) | `custom-user-preferences/` (nutrition assistant, user-preference strategy) and `episodic-memory/` (nutrition assistant, episodic strategy), each with hand-written pre/post hooks. |
| **Memory as a tool** — the model decides via tool calls | [`03-memory-as-tool/`](./03-memory-as-tool/) | `store_memory` / `recall_memory` exposed as LangGraph `@tool`s; the agent calls them in the ReAct loop. |

See the [long-term memory README](../../../README.md) for the underlying strategies and APIs.

## Both classes, one memory resource

`langgraph-checkpoint-aws` ships two classes, and long-term memory needs only one of them:

> **`AgentCoreMemoryStore` (`store=`) recalls facts about _this user_. `AgentCoreMemorySaver`
> (`checkpointer=`) resumes _this conversation_.**

They are separate arguments, not alternatives, and `01-built-in-callback/` and both
`02-custom-callback/` scripts wire them against a **single `memory_id`** — the production shape:

```python
store        = AgentCoreMemoryStore(memory_id=memory_id, region_name=region)  # keyword-only
checkpointer = AgentCoreMemorySaver(memory_id, region_name=region)            # positional
```

Read this [checkpointer vs. store](../../../../00-getting-started/06-usage-patterns.md#langgraph-specifically-checkpointer-vs-store) for full comparison and the gotchas —
`InvalidConfigError` if `actor_id` is missing, `put` vs. `search` namespaces.

> ### LangGraph API versions
> The new tutorials (**`01-built-in-callback`**, **`03-memory-as-tool`**) target the
> current **LangGraph v1.0** API: `from langchain.agents import create_agent` plus the
> **middleware** system (`@before_model`, `@after_model`, `@dynamic_prompt`) from
> `langchain.agents.middleware`. The **`02-custom-callback`** examples still use the older
> `langgraph.prebuilt.create_react_agent` with `pre_model_hook` / `post_model_hook`, which
> is **deprecated** in v1.0 but still functional. Both styles run today; new code should
> prefer `create_agent` + middleware. Each folder's `requirements.txt` pins what it needs.

## Running the Python Scripts

Install dependencies and run scripts from the relevant sub-folders. Each sub-folder has its
own `requirements.txt`:

```bash
# Built-in callback (AgentCoreMemorySaver + AgentCoreMemoryStore via middleware)
cd 01-built-in-callback
pip install -r requirements.txt
python langgraph-ltm-built-in-callback.py

# Custom callback (hand-rolled hooks + custom strategy override)
cd ../02-custom-callback/episodic-memory
pip install -r requirements.txt
python nutrition-assistant-with-episodic-memory.py
# or:
cd ../custom-user-preferences
pip install -r requirements.txt
python nutrition-assistant-with-user-preference-saving.py

# Memory as a tool (store_memory / recall_memory as LangGraph tools)
cd ../../03-memory-as-tool
pip install -r requirements.txt
python langgraph-ltm-memory-tool.py
```

Multi-agent variant (multiple LangGraph agents sharing one memory resource):
[`../../multi-agent/with-langgraph-agent/`](../../multi-agent/with-langgraph-agent/).
