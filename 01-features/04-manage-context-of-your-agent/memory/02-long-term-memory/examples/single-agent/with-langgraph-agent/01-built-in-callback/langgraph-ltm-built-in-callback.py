# # LangGraph with AgentCore Memory — Built-in Callback (Long-term Memory)
#
# ## Introduction
#
# This tutorial demonstrates the **lowest-effort** way to give a LangGraph agent
# long-term memory: let the `langgraph-checkpoint-aws` package wire AgentCore Memory
# into the agent's lifecycle for you. Two AgentCore integrations do the work, and your
# code never calls `create_event` / `retrieve_memories` directly:
#
# 1. **`AgentCoreMemorySaver`** — a LangGraph **checkpointer** backed by AgentCore
#    Memory. Once you pass it to the agent, conversation **state is persisted and
#    resumed automatically** at every super-step, keyed by `thread_id` (session) and
#    `actor_id`. No save/load code at all.
# 2. **`AgentCoreMemoryStore`** — a LangGraph **store** backed by AgentCore Memory's
#    long-term strategies. We attach two tiny **middleware callbacks** (`@after_model`
#    to persist each turn, `@dynamic_prompt` to inject recalled facts). The store turns
#    `store.put(...)` into a `create_event` and `store.search(...)` into a
#    `retrieve_memories` under the hood, and the AgentCore **Semantic strategy** does the
#    extraction asynchronously in the background.
#
# This is the "framework handles the lifecycle" pattern: you declare *where* memory
# hooks into the graph and the integration does the rest. Compare with
# [`02-custom-callback/`](../02-custom-callback/), where the callbacks hand-roll the
# retrieval, context injection, and storage logic themselves.
#
# > **LangGraph API note (v1.0):** `langgraph.prebuilt.create_react_agent` and its
# > `pre_model_hook` / `post_model_hook` arguments are **deprecated** in LangGraph v1.0.
# > The current API is `from langchain.agents import create_agent` with the
# > **middleware** system (`@before_model`, `@after_model`, `@dynamic_prompt`,
# > `@wrap_model_call`) from `langchain.agents.middleware`. This tutorial uses the
# > current API. Middleware callbacks receive `(state, runtime)` — not `config` — so we
# > carry the per-user identity (`actor_id`, `session_id`) in a typed **runtime context**
# > (`context_schema`), and the checkpointer reads `thread_id` / `actor_id` from the
# > runtime `config` as it always has.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                         |
# | Agent usecase       | Nutrition Assistant                                                              |
# | Agentic Framework   | LangGraph (v1.0 `create_agent` + middleware)                                     |
# | LLM model           | Anthropic Claude Haiku 4.5 (via Amazon Bedrock)                                  |
# | Tutorial components | AgentCoreMemorySaver (checkpointer), AgentCoreMemoryStore, before/after-model middleware, built-in Semantic strategy |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Use `AgentCoreMemorySaver` as a **checkpointer** so conversation state persists automatically
# - Use `AgentCoreMemoryStore` as the long-term backend with **minimal** callback wiring
# - Write v1.0 **middleware** (`@after_model`, `@dynamic_prompt`) instead of deprecated pre/post hooks
# - Pass per-user identity through a typed **runtime context** (`context_schema`)
# - Let the AgentCore Semantic strategy extract durable facts in the background
#
# ### Scenario Context
#
# We build a **Nutrition Assistant** that remembers user context (dietary restrictions,
# goals, preferences) across sessions. The user shares facts in session 1; after the
# Semantic strategy extracts them, a brand-new session 2 recalls them automatically —
# the `@dynamic_prompt` callback injects the recalled facts before the model runs.
#
# ## Architecture
#
# ```
#   ┌───────────────────────────── create_agent (LangGraph v1.0) ─────────────────────────────┐
#   │                                                                                          │
#   │   @dynamic_prompt  ──▶  model (Bedrock)  ──▶  @after_model  ──▶  (tools, if any)          │
#   │   (inject recalled        Claude Haiku        (persist the                                │
#   │    facts into the          4.5                 turn for                                   │
#   │    system prompt)                              extraction)                                │
#   │        │                                            │                                     │
#   └────────┼────────────────────────────────────────────┼─────────────────────────────────────┘
#            │ store.search(("users", actor, "facts/"))    │ store.put((actor, session), msg)
#            ▼                                              ▼
#   ┌──────────────────────────────────────────────────────────────────────────────────────────┐
#   │  AgentCore Memory  (one resource)                                                          │
#   │                                                                                            │
#   │   AgentCoreMemoryStore   ── create_event ─▶ short-term events ─▶ async Semantic extraction  │
#   │                          ◀─ retrieve_memories ── long-term records in /users/{actor}/facts/ │
#   │                                                                                            │
#   │   AgentCoreMemorySaver   ── checkpoints graph state per (thread_id, actor_id)  [automatic]  │
#   └──────────────────────────────────────────────────────────────────────────────────────────┘
# ```
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS account with appropriate permissions
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - Access to the Claude Haiku 4.5 model in Amazon Bedrock (request it in the Bedrock
#   console under *Model access*, in your chosen region)
# - No IAM execution role required — we use a **built-in** Semantic strategy, for which
#   AgentCore manages the extraction/consolidation models.
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import logging
import os
import time
import uuid
from dataclasses import dataclass

# AgentCore Memory control-plane client (create the resource) + the StrategyType enum
# (gives us the exact wire key for the Semantic strategy).
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# LangGraph v1.0 agent factory + middleware. NOTE: this replaces the deprecated
# `from langgraph.prebuilt import create_react_agent` and its pre/post model hooks.
from langchain.agents import create_agent
from langchain.agents.middleware import after_model, dynamic_prompt
from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, HumanMessage

# The AgentCore Memory integrations for LangGraph (the `langgraph-checkpoint-aws`
# package). `AgentCoreMemorySaver` is the checkpointer; `AgentCoreMemoryStore` is the
# long-term store. Both are thin adapters over the AgentCore Memory data-plane API.
from langgraph_checkpoint_aws import AgentCoreMemorySaver, AgentCoreMemoryStore

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("langgraph-ltm-built-in")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory

# Model ID for Claude Haiku 4.5 on Amazon Bedrock. The `global.` prefix selects the
# global (cross-region) inference endpoint. To pin traffic to a region instead, swap the
# prefix (e.g. "us.anthropic.claude-haiku-4-5-20251001-v1:0").
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

# One end-user. Two sessions: the first seeds memory, the second (a "new conversation")
# recalls across the gap. Both share the same ACTOR_ID — that's what ties a user's
# long-term records together across sessions.
ACTOR_ID = "user-1"
SEED_SESSION_ID = "nutrition-session-1"
NEW_SESSION_ID = "nutrition-session-2"

# Namespace template for the extracted Semantic records. `{actorId}` is substituted at
# extraction time, keeping every user's facts isolated. The `@dynamic_prompt` callback
# reads from the resolved prefix (see Step 4). Always end the template with "/".
SEMANTIC_NAMESPACE = "/users/{actorId}/facts/"

# Long-term extraction is asynchronous — records appear ~30-90s after a turn is saved.
# We poll for them (Step 6) but cap the wait so the demo always finishes.
EXTRACTION_MAX_WAIT_SECONDS = 120
EXTRACTION_POLL_INTERVAL_SECONDS = 15


# ## Step 2: The runtime context (how identity reaches the callbacks)
#
# In LangGraph v1.0, middleware callbacks receive `(state, runtime)` — they do NOT get
# the invocation `config`. Per-run, per-user values therefore travel in a typed
# **runtime context** object that we declare here and pass to `create_agent` via
# `context_schema=`. We supply an instance at invoke time with `context=...`, and the
# callbacks read `runtime.context.actor_id` / `runtime.context.session_id`.


@dataclass
class MemoryContext:
    """Per-invocation identity made available to middleware as `runtime.context`."""

    actor_id: str
    session_id: str


# ## Step 3: Initialize the clients, the store, and the checkpointer
#
# `AgentCoreMemoryStore` takes `memory_id` as a keyword argument; `AgentCoreMemorySaver`
# takes it positionally (both forward extra kwargs such as `region_name` to boto3). They
# are created once and reused across sessions — what changes per session is the runtime
# context and config, not the integrations.


memory_client = MemoryClient(region_name=REGION)


# ## Step 4: The memory middleware (the "built-in callback")
#
# Two small callbacks are the entire long-term-memory wiring. The integration does the
# heavy lifting: `store.put` becomes a `create_event`, `store.search` becomes a
# `retrieve_memories`, and the Semantic strategy extracts durable facts in the
# background. We never touch the AgentCore API directly here.


@dynamic_prompt
def inject_recalled_facts(request) -> str:
    """BEFORE the model runs: search long-term memory and fold any hits into the prompt.

    `@dynamic_prompt` is the v1.0 convenience middleware for building the system prompt
    per request. It receives a `ModelRequest` exposing `.state`, `.runtime.context`, and
    `.runtime.store`. We search the user's Semantic namespace with the latest human
    message as the query and append whatever we recall to the base system prompt.
    """
    base_prompt = (
        "You are a knowledgeable, friendly nutrition assistant. Give concise, practical "
        "advice. Personalize using any remembered facts about the user shown below."
    )

    store = request.runtime.store
    ctx: MemoryContext = request.runtime.context
    if store is None or ctx is None:
        return base_prompt

    # The latest human message is the search query.
    messages = request.state.get("messages", [])
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if last_human is None:
        return base_prompt

    # Search the resolved Semantic namespace. The store turns this tuple into the string
    # "/users/<actor>/facts/" and calls retrieve_memories for us. The last tuple element
    # keeps its trailing "/" so it matches the strategy's namespace prefix.
    namespace = ("users", ctx.actor_id, "facts/")
    try:
        hits = store.search(namespace, query=last_human.text, limit=5)
    # Recall is best-effort: never let a memory failure break the turn.
    except Exception as e:  # noqa: BLE001
        logger.warning(f"recall failed (continuing without memory): {e}")
        return base_prompt

    if not hits:
        logger.info("🔎 no long-term facts recalled yet")
        return base_prompt

    # Each hit's value carries the extracted text under "content" (see store.py).
    facts = [item.value.get("content", "").strip() for item in hits]
    facts = [f for f in facts if f]
    logger.info(f"🔎 recalled {len(facts)} long-term fact(s)")
    remembered = "\n".join(f"- {f}" for f in facts)
    return f"{base_prompt}\n\nWhat you remember about this user:\n{remembered}"


@after_model
def persist_turn(state, runtime):
    """AFTER the model responds: persist the latest user + assistant messages.

    `@after_model` is the v1.0 replacement for `post_model_hook`. It receives
    `(state, runtime)` and returns state updates or `None`. We write the newest human
    and AI messages to the store; the store converts each into a `create_event`, which
    feeds the asynchronous Semantic extraction pipeline. We return `None` because we are
    not modifying the agent state — only persisting a side copy to memory.
    """
    store = runtime.store
    ctx: MemoryContext = runtime.context
    if store is None or ctx is None:
        return

    # The store REQUIRES a 2-tuple namespace (actor_id, session_id) for writes — this is
    # the raw short-term event; the strategy later extracts it into /users/<actor>/facts/.
    namespace = (ctx.actor_id, ctx.session_id)
    messages = state.get("messages", [])

    # Persist the most recent human turn and the most recent AI turn (if any).
    for msg_type in (HumanMessage, AIMessage):
        msg = next((m for m in reversed(messages) if isinstance(m, msg_type)), None)
        if msg is not None and msg.text.strip():
            try:
                store.put(namespace, str(uuid.uuid4()), {"message": msg})
            # Persistence is best-effort: don't crash the turn on a save failure.
            except Exception as e:  # noqa: BLE001
                logger.warning(f"failed to persist {msg_type.__name__}: {e}")
    logger.info("🧠 persisted turn to AgentCore Memory (queued for extraction)")


# ## Step 5: Create the memory resource (built-in Semantic strategy)
#
# We create one memory with a single built-in **Semantic** strategy whose namespace
# template is `/users/{actorId}/facts/`. Built-in strategies require NO IAM execution
# role — AgentCore manages the extraction/consolidation models. The SDK's
# `create_or_get_memory` reuses the resource by name if it already exists.


def get_or_create_memory(name: str) -> str:
    """Create a memory with a built-in Semantic strategy, or reuse it if it exists."""
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "NutritionFacts",
                "description": "Durable facts about the user: diet, goals, preferences",
                "namespaceTemplates": [SEMANTIC_NAMESPACE],
            }
        }
    ]
    # create_or_get_memory creates the resource, or returns the existing one (by name)
    # if it already exists — so we don't have to catch "already exists" ourselves.
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # strategies => long-term extraction is enabled
        description="LangGraph built-in-callback LTM tutorial (Nutrition Assistant)",
        event_expiry_days=7,  # retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with built-in Semantic strategy ready: {memory_id}")
    return memory_id


def wait_for_extraction(memory_id: str) -> None:
    """Poll until long-term records appear, or until the max wait elapses.

    In production you would NOT block like this — the agent simply recalls on the user's
    next visit, by which point extraction has long completed. We block here only so the
    demo's second session has data to recall.
    """
    logger.info("⏳ Waiting for asynchronous Semantic extraction to complete...")
    namespace = SEMANTIC_NAMESPACE.format(actorId=ACTOR_ID)
    deadline = time.time() + EXTRACTION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            records = memory_client.retrieve_memories(
                memory_id=memory_id,
                namespace=namespace,
                query="user dietary facts and goals",
                top_k=1,
            )
            if records:
                logger.info("✅ Long-term records are available")
                return
        # The probe is only a poll: any failure just means "retry until the deadline".
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Retrieval probe failed (will retry): {e}")
        time.sleep(EXTRACTION_POLL_INTERVAL_SECONDS)
    logger.warning(
        "⚠️ Extraction did not surface records within the wait window. "
        "It may still complete shortly — try re-running retrieval later."
    )


# ## Step 6: Build the agent and run the demo
#
# `create_agent` wires the model, the two memory callbacks (middleware), the
# `AgentCoreMemoryStore`, and the `AgentCoreMemorySaver` checkpointer. At invoke time we
# pass BOTH:
#   • `config={"configurable": {"thread_id", "actor_id"}}` — read by the CHECKPOINTER
#     (`AgentCoreMemorySaver`) to persist/resume graph state.
#   • `context=MemoryContext(...)` — read by the MIDDLEWARE callbacks (which only get
#     `runtime`, not `config`).
# `thread_id` maps to the AgentCore session_id and `actor_id` to the AgentCore actor_id.


def run_turn(graph, actor_id: str, session_id: str, user_text: str) -> str:
    """Invoke the agent for one user turn and return the assistant's final text."""
    config = {
        "configurable": {
            "thread_id": session_id,  # REQUIRED by the checkpointer (maps to AgentCore session_id)
            "actor_id": actor_id,  # REQUIRED by the checkpointer (maps to AgentCore actor_id)
        }
    }
    context = MemoryContext(actor_id=actor_id, session_id=session_id)
    result = graph.invoke(
        {"messages": [{"role": "user", "content": user_text}]},
        config=config,
        context=context,
    )
    return result["messages"][-1].text


def main() -> None:
    memory_id = None  # init so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("LangGraphBuiltInCallback")

        # Build the long-term store and the checkpointer over the SAME memory resource.
        # AgentCoreMemoryStore: memory_id is keyword-only. AgentCoreMemorySaver: positional.
        store = AgentCoreMemoryStore(memory_id=memory_id, region_name=REGION)
        checkpointer = AgentCoreMemorySaver(memory_id, region_name=REGION)

        # Initialize the Bedrock-backed chat model. `init_chat_model(..., "bedrock_converse")`
        # is the same path the sibling LTM tutorials use; `ChatBedrock` from `langchain_aws`
        # is an equivalent drop-in.
        llm = init_chat_model(MODEL_ID, model_provider="bedrock_converse", region_name=REGION)

        # Assemble the agent with the v1.0 factory. The two callbacks are passed as
        # middleware; the store and checkpointer make memory automatic; context_schema
        # lets the callbacks read per-user identity from runtime.context.
        graph = create_agent(
            model=llm,
            tools=[],  # no extra tools — memory is handled by the callbacks
            middleware=[inject_recalled_facts, persist_turn],
            store=store,
            checkpointer=checkpointer,
            context_schema=MemoryContext,
        )

        # ---- Session 1: the user shares durable facts -----------------------------
        print("\n=== Session 1 (the callbacks persist each turn automatically) ===")
        for user_text in [
            "Hi! I'm Sam. I'm vegetarian and allergic to shellfish.",
            "I'm training for a half marathon in October, so I'm watching my protein intake.",
        ]:
            print(f"User:  {user_text}")
            reply = run_turn(graph, ACTOR_ID, SEED_SESSION_ID, user_text)
            print(f"Agent: {reply}\n")

        # ---- Wait for the Semantic extraction pipeline ----------------------------
        wait_for_extraction(memory_id)

        # ---- Session 2: a NEW session recalls across the gap ----------------------
        # New thread_id => fresh checkpointed state. The only way the agent can
        # personalize is the @dynamic_prompt callback recalling facts from session 1.
        print("=== Session 2 (new session — the callback recalls facts automatically) ===")
        for user_text in [
            "Can you suggest a high-protein dinner I could make tonight?",
        ]:
            print(f"User:  {user_text}")
            reply = run_turn(graph, ACTOR_ID, NEW_SESSION_ID, user_text)
            print(f"Agent: {reply}\n")

    finally:
        # ## Cleanup
        #
        # AgentCore Memory resources are billable, so we delete the resource when the
        # demo finishes. To keep the memory between runs (e.g. to inspect it in the
        # console), comment out the block below — `get_or_create_memory` will reuse it.
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info(f"✅ Deleted memory: {memory_id}")
            # Cleanup runs in a finally block: report the failure, never mask the
            # original exception (if any) by raising from here.
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to delete memory {memory_id}: {e}")


if __name__ == "__main__":
    main()
