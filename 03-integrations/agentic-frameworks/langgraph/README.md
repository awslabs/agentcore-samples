# LangGraph on AgentCore: hosting, memory and tracing

**Start with the [LangGraph + Bedrock Runtime tutorial](../../../01-features/02-host-your-agent/01-runtime/01-hosting-agents/01-http-protocol/02-langgraph-bedrock/).** It builds a `StateGraph`: a model selects a tool, `ToolNode` executes it, and the result flows back to the model for a final answer. The tutorial includes local invocation, deployment and cleanup using direct service APIs. It replaces this directory's older Starter Toolkit walkthrough. It teaches routing with a calculator and fixed weather tool, **not live DuckDuckGo search**.

## When do I need a checkpointer or a store?

A graph compiled without either is stateless across invocations. Reusing a Runtime session ID does not, by itself, restore its messages.

| Goal | LangGraph integration | Worked example |
|---|---|---|
| Resume the **same conversation**, including a paused graph | Pass `AgentCoreMemorySaver` as `checkpointer=` to `compile()`. Supply a `thread_id` and `actor_id` in the invocation's `configurable` settings. | [Short-term memory and checkpointing](../../../01-features/04-manage-context-of-your-agent/memory/01-short-term-memory/examples/single-agent/with-langgraph-agent/) |
| Recall facts for the **same user across conversations** | Pass `AgentCoreMemoryStore` as `store=`. Configure a long-term memory strategy; write turns and search the strategy's namespace when you need recall. | [Long-term memory with LangGraph](../../../01-features/04-manage-context-of-your-agent/memory/02-long-term-memory/examples/single-agent/with-langgraph-agent/) |

The two can use the same memory resource. A checkpointer restores graph state; a store is not automatically searched, and extraction is asynchronous. In a multi-user application, derive actor and session bindings from authenticated application state, not an arbitrary `actor_id` in a request body. The basic hosting tutorial does not create a memory resource.

## Already hosting elsewhere?

The [LangGraph observability example](observability/) instruments an agent running **outside** AgentCore Runtime. Its setup and requirements are independent of the hosting tutorial. For existing deployments of the retired web-search example, see the [historical source](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/agentic-frameworks/langgraph); repository changes do not remove deployed resources.
