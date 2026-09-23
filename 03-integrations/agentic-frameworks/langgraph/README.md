# LangGraph Agent with Bedrock AgentCore Integration

| Information         | Details                                                                      |
|---------------------|------------------------------------------------------------------------------|
| Agent type          | Synchronous                                                                 |
| Agentic Framework   | Langgraph                                                                    |
| LLM model           | Anthropic Claude 3 Haiku                                                     |
| Components          | AgentCore Runtime                                |
| Example complexity  | Easy                                                                 |
| SDK used            | Amazon BedrockAgentCore Python SDK                                           |

This example demonstrates how to integrate a LangGraph agent with AWS Bedrock AgentCore, enabling you to deploy a web search-capable agent as a managed service.

## Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver
- AWS account with Bedrock access

## Setup Instructions

### 1. Create a Python Environment with uv

```bash
# Install uv if you don't have it already
pip install uv

# Create and activate a virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Requirements

```bash
uv pip install -r requirements.txt
```

### 3. Understanding the Agent Code

The `langgraph_agent_web_search.py` file contains a LangGraph agent with web search capabilities, integrated with Bedrock AgentCore:

```python
from typing import Annotated
from langchain.chat_models import init_chat_model
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

# Initialize the LLM with Bedrock
llm = init_chat_model(
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    model_provider="bedrock_converse",
)

# Define search tool
from langchain_community.tools import DuckDuckGoSearchRun
search = DuckDuckGoSearchRun()
tools = [search]
llm_with_tools = llm.bind_tools(tools)

# Define state
class State(TypedDict):
    messages: Annotated[list, add_messages]

# Build the graph
graph_builder = StateGraph(State)

def chatbot(state: State):
    return {"messages": [llm_with_tools.invoke(state["messages"])]}

graph_builder.add_node("chatbot", chatbot)
tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_conditional_edges("chatbot", tools_condition)
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge(START, "chatbot")
graph = graph_builder.compile()

# Integrate with Bedrock AgentCore
from bedrock_agentcore.runtime import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
def agent_invocation(payload, context):
    tmp_msg = {"messages": [{"role": "user", "content": payload.get("prompt", "No prompt found in input")}]}
    tmp_output = graph.invoke(tmp_msg)
    return {"result": tmp_output['messages'][-1].content}

app.run()
```

### 4. Configure and Launch with Bedrock AgentCore Toolkit

```bash
# Configure your agent for deployment
agentcore configure

# Deploy your agent
agentcore launch -e langgraph_agent_web_search.py
```

During configuration, you'll be prompted to:
- Select your AWS region
- Choose a deployment name
- Configure other deployment settings

### 5. Testing Your Agent

Once deployed, you can test your agent using:

```bash
agentcore invoke {"prompt":"What are the latest developments in quantum computing?"}
```

The agent will:
1. Process your query
2. Use DuckDuckGo to search for relevant information
3. Provide a comprehensive response based on the search results

### 6. Cleanup

To remove your deployed agent:

```bash
agentcore destroy
```

## How It Works

This agent uses LangGraph to create a directed graph for agent reasoning:

1. The user query is sent to the chatbot node
2. The chatbot decides whether to use tools based on the query
3. If tools are needed, the query is sent to the tools node
4. The tools node executes the search and returns results
5. Results are sent back to the chatbot for final response generation

The Bedrock AgentCore framework handles deployment, scaling, and management of the agent in AWS.

## Adding memory: checkpointer vs. store

The graph above is compiled with `graph_builder.compile()` — **no checkpointer**. That makes
it stateless: every `agentcore invoke` starts from an empty message list, so the agent
remembers nothing from the previous turn.

To fix that, install
[`langgraph-checkpoint-aws`](https://pypi.org/project/langgraph-checkpoint-aws/) and back the
graph with AgentCore Memory. It gives you **two** classes. The names are similar, so the one
thing to remember is:

> **The checkpointer remembers _this conversation_. The store remembers _this user_.**

They are not alternatives — they fill two different LangGraph arguments and can point at the
same memory resource:

```python
graph = graph_builder.compile(
    checkpointer=AgentCoreMemorySaver(memory_id),         # this conversation
    store=AgentCoreMemoryStore(memory_id=memory_id),      # this user, across conversations
)
```

| | `AgentCoreMemorySaver` (checkpointer) | `AgentCoreMemoryStore` (store) |
|---|---|---|
| **Use it for** | Short-term memory | Long-term memory |
| **Answers** | "Resume this conversation where it left off" | "What do I know about this user from *other* conversations?" |
| **Saves** | The whole graph state — every message, tool call, and paused `interrupt()` — exactly as-is | One message at a time, for AgentCore to extract facts from later |
| **Reads back** | Automatically, on the next `invoke` with the same `thread_id` | When you call `store.search(...)` |
| **Needs a memory strategy?** | No | Yes — Semantic, User Preference, Summary, or a custom one |

Most production agents use both. Note the store is *not* a faster checkpointer: extraction
runs in the background, so a fact written this turn is usually not searchable on the next
turn.

### Wiring both into this sample

Pass both objects to `compile()`, then pass the Runtime's session id as the LangGraph
`thread_id`. **The checkpointer needs both `thread_id` and `actor_id`** in `configurable`
and raises `InvalidConfigError` if either is missing:

```python
import uuid
from langgraph_checkpoint_aws import AgentCoreMemorySaver, AgentCoreMemoryStore

# `memory_id` is positional on the saver and keyword-only on the store.
checkpointer = AgentCoreMemorySaver(MEMORY_ID, region_name=REGION)
store = AgentCoreMemoryStore(memory_id=MEMORY_ID, region_name=REGION)

graph = graph_builder.compile(checkpointer=checkpointer, store=store)

@app.entrypoint
def agent_invocation(payload, context):
    # context.session_id is None when the caller omits the session header (a plain
    # local curl, for example), and the checkpointer rejects an empty thread_id.
    session_id = context.session_id or str(uuid.uuid4())
    actor_id = payload.get("actor_id", "default-user")
    config = {
        "configurable": {
            "thread_id": session_id,   # → AgentCore sessionId
            "actor_id": actor_id,      # → AgentCore actorId
        }
    }
    result = graph.invoke(
        {"messages": [{"role": "user", "content": payload.get("prompt", "")}]},
        config,
    )
    return {"result": result["messages"][-1].content}
```

The conversation now lives in AgentCore Memory instead of in the container, so it survives
across Runtime invocations — and a paused `interrupt()` can be resumed by a different
container than the one that paused it.

The `store=` argument is not read automatically, though. LangGraph injects it into nodes,
middleware, and tools, and *you* decide when to write and when to recall — for example a node
that recalls the user's known facts before the model runs and records the turn after:

```python
from langgraph.store.base import BaseStore

def recall_and_record(state, config, *, store: BaseStore):
    actor_id = config["configurable"]["actor_id"]
    session_id = config["configurable"]["thread_id"]

    # RECALL: search the *strategy's* namespace. This must match the namespace template
    # on the strategy you attached to MEMORY_ID, e.g. "/users/{actorId}/facts/".
    hits = store.search(("users", actor_id, "facts/"), query=state["messages"][-1].content, limit=3)
    known = "\n".join(h.value.get("content", "") for h in hits)

    # RECORD: store.put takes exactly a 2-tuple, (actor_id, session_id). AgentCore extracts
    # facts from it asynchronously, so what you write now is searchable on a *later* turn.
    store.put((actor_id, session_id), str(uuid.uuid4()), {"message": state["messages"][-1]})

    return {"messages": [{"role": "system", "content": f"Known about this user:\n{known}"}]} if known else {}
```

LangGraph passes the store to any node that declares a `store` parameter — you don't thread it
through yourself. Wire the node in ahead of `chatbot`, so START flows through it first:

```python
graph_builder.add_node("memory", recall_and_record)
graph_builder.add_edge(START, "memory")     # replaces add_edge(START, "chatbot")
graph_builder.add_edge("memory", "chatbot")
```

Two things to keep in mind:

- **The store needs a memory strategy on `MEMORY_ID`; the checkpointer does not.** Nothing is
  ever extracted without one, so `store.search` would always come back empty.
- **One memory resource is enough for both.** The saver writes opaque `blob` payloads that no
  strategy reads, while the store writes `conversational` payloads that strategies extract — so
  checkpoint data never pollutes your extracted records.

Full, runnable tutorials live in the memory feature folder:

- [Short-term memory with LangGraph](../../../01-features/04-manage-context-of-your-agent/memory/01-short-term-memory/examples/single-agent/with-langgraph-agent/) — `AgentCoreMemorySaver`, including a checkpointed human-in-the-loop example
- [Long-term memory with LangGraph](../../../01-features/04-manage-context-of-your-agent/memory/02-long-term-memory/examples/single-agent/with-langgraph-agent/) — `AgentCoreMemoryStore` via built-in callbacks, custom hooks, or tools

## Additional Resources

- [LangGraph Documentation](https://github.com/langchain-ai/langgraph)
- [LangChain Documentation](https://python.langchain.com/docs/get_started/introduction)
- [`langgraph-checkpoint-aws` source](https://github.com/langchain-ai/langchain-aws/tree/main/libs/langgraph-checkpoint-aws) — the `AgentCoreMemorySaver` / `AgentCoreMemoryStore` implementations, in the [`langchain-aws`](https://github.com/langchain-ai/langchain-aws) monorepo
- [Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-core.html)
