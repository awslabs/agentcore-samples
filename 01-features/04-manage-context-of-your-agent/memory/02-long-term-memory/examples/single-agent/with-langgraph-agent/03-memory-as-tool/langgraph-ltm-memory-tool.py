# # LangGraph with AgentCore Memory — Memory as a Tool (Long-term Memory)
#
# ## Introduction
#
# This tutorial demonstrates how to expose AgentCore **long-term memory** as **tools the
# model decides to call** inside a LangGraph agent. The companion tutorials wired memory
# in *around* the model: [`01-built-in-callback/`](../01-built-in-callback/) persisted and
# recalled on every turn via middleware; [`02-custom-callback/`](../02-custom-callback/)
# did the same with hand-rolled hooks. In both, **your code** (a callback) decided when to
# save and when to recall.
#
# Here we hand that decision to the agent. We define two LangGraph tools —
# `store_memory` and `recall_memory` — and pass them to `create_agent`. The agent runs the
# standard **ReAct loop**: the model emits a tool call, LangGraph's `ToolNode` executes it,
# the result goes back to the model, and it continues until it produces a final answer. The
# tool implementations call AgentCore Memory directly: `create_event` to store,
# `retrieve_memories` to recall.
#
# > **LangGraph API note (v1.0):** This tutorial uses the current
# > **`from langchain.agents import create_agent`** API. The older
# > `langgraph.prebuilt.create_react_agent` is deprecated in LangGraph v1.0 (it still
# > runs, but emits a deprecation warning). `create_agent` builds the same tool-calling
# > ReAct loop — model ⇄ `ToolNode` — and accepts a `tools=` list directly.
#
# ## Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                         |
# | Agent usecase       | Personal Assistant                                                               |
# | Agentic Framework   | LangGraph (v1.0 `create_agent`)                                                  |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Tutorial components | LangGraph tools (`@tool`), ToolNode ReAct loop, built-in Semantic strategy, create_event, retrieve_memories |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Define memory operations (`store_memory`, `recall_memory`) as LangGraph `@tool`s
# - Let the model decide WHEN to save a durable fact and WHEN to search its past knowledge
# - Back those tools with AgentCore Memory: `create_event` to store, `retrieve_memories` to recall
# - Run the tool-calling ReAct loop with `create_agent`
# - Show cross-session recall: a fresh session where the agent recovers context by calling `recall_memory`
#
# ## Memory-as-tool vs. the callback patterns
#
# | | 01 — Built-in callback | 02 — Custom callback | 03 — Memory as a tool (this) |
# |---|---|---|---|
# | **Who decides to store** | The framework callback, every turn | Your hook, every turn | **The model**, via `store_memory` |
# | **Who decides to recall** | The framework callback, every turn | Your hook, every turn | **The model**, via `recall_memory` |
# | **How memory reaches the model** | Injected into the prompt by a callback | Injected by your hook | Returned as a `ToolMessage` the model requested |
# | **Control flow** | Linear (recall → model → persist) | Linear | **ReAct loop** (model drives) |
# | **Best when** | You want deterministic, always-on memory | …plus customized extraction | The model should manage its own memory lifecycle |
#
# ## Architecture
#
# ```
#   ┌───────────────────────── create_agent (LangGraph ReAct loop) ─────────────────────────┐
#   │                                                                                        │
#   │     model (Bedrock, Claude Sonnet 4.6)  ──tool call──▶  ToolNode                        │
#   │            ▲                                              │                             │
#   │            │  ToolMessage (result)                        │  store_memory / recall_memory│
#   │            └──────────────────────────────────────────────┘                             │
#   └────────────────────────────────────────────────────────────┼──────────────────────────┘
#                                                                  │
#              store_memory → create_event                         │
#              recall_memory → retrieve_memories                   ▼
#   ┌────────────────────────────────────────────────────────────────────────┐
#   │  AgentCore Memory                                                        │
#   │   create_event ──▶ short-term events ──▶ async Semantic extraction ──▶   │
#   │   long-term records ◀── retrieve_memories (semantic search by namespace) │
#   └────────────────────────────────────────────────────────────────────────┘
# ```
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - Access to the Claude Sonnet 4.6 model in Amazon Bedrock (request it in the Bedrock
#   console under *Model access*, in your chosen region)
# - No IAM execution role required — we use a **built-in** Semantic strategy.
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import logging
import os
import time
from datetime import datetime, timezone

# AgentCore Memory client + the StrategyType enum (exact strategy wire key).
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# LangGraph v1.0 agent factory (replaces the deprecated create_react_agent).
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("langgraph-ltm-memory-tool")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock. The `global.` prefix selects the
# global (cross-region) inference endpoint. To pin to a region, swap the prefix
# (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# One end-user. Two sessions: the first seeds memory, the second recalls across the gap.
# Both share the same ACTOR_ID — that's what ties a user's long-term records together.
ACTOR_ID = "user_789"
SEED_SESSION_ID = "assistant_session_001"
NEW_SESSION_ID = "assistant_session_002"

# Namespace template for the Semantic records. `{actorId}` is substituted at extraction
# time, isolating each user's facts. `recall_memory` retrieves from the resolved path.
SEMANTIC_NAMESPACE = "/users/{actorId}/facts/"

# Long-term extraction is asynchronous — records appear ~30-90s after create_event. We
# poll for them (Step 5) but cap the total wait so the demo always finishes.
EXTRACTION_MAX_WAIT_SECONDS = 120
EXTRACTION_POLL_INTERVAL_SECONDS = 15

# The system prompt tells the agent it HAS memory tools and WHEN to use them. With the
# memory-as-tool pattern this prompt does the steering that the callback tutorials did in
# code — be explicit so the model reaches for the tools on its own.
SYSTEM_PROMPT = (
    "You are a helpful personal assistant with persistent long-term memory.\n"
    "\n"
    "You have two memory tools:\n"
    "- store_memory: save a durable fact about the user so you can recall it in future "
    "conversations. Call it whenever the user shares something worth remembering long-term "
    "— their name, preferences, goals, constraints, important dates. Do NOT store transient "
    "small talk.\n"
    "- recall_memory: search your long-term memory for facts you saved previously. Call it "
    "at the START of a conversation, and any time answering well depends on something the "
    "user may have told you before.\n"
    "\n"
    "Use these tools proactively and on your own judgment — the user will not remind you. "
    "Be friendly and concise. "
    f"Today's date: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')}."
)

# A single MemoryClient is reused by both tools (created in Step 2).
memory_client = MemoryClient(region_name=REGION)
logger.info(f"✅ MemoryClient initialized for region: {REGION}")


# ## Step 2: Define the memory tools
#
# Each tool is a plain Python function decorated with LangGraph's `@tool`. The docstring
# IS the description the model sees — it is load-bearing, because the model decides
# whether and when to call a tool almost entirely from it, so we state the trigger
# conditions explicitly.
#
# The tools are built by a factory that closes over the `memory_id` and `session_id` for
# this run. (LangGraph can also inject runtime state into tools, but closing over the IDs
# keeps the AgentCore mechanics front-and-center and matches the other tutorials.)


def build_memory_tools(memory_id: str, session_id: str):
    """Create the store/recall tools bound to a specific memory resource and session."""

    @tool
    def store_memory(fact: str) -> str:
        """Save an important, durable fact about the user to long-term memory so it can be
        recalled in future conversations. Call this whenever the user shares information
        worth remembering across sessions — their name, preferences, goals, constraints,
        or notable personal details. Do NOT call it for transient small talk.

        Args:
            fact: A concise, self-contained statement of the fact to remember, written in
                the third person. Example: "The user is vegetarian and allergic to shellfish."
        """
        # create_event writes the fact as a single USER message. Because the memory has a
        # Semantic strategy, this event feeds the asynchronous extraction pipeline — there
        # is no separate "extract" call.
        memory_client.create_event(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=session_id,
            messages=[(fact, "USER")],
        )
        logger.info(f"🧠 store_memory: queued for extraction — {fact!r}")
        return f"Saved to long-term memory: {fact}"

    @tool
    def recall_memory(query: str) -> str:
        """Search your long-term memory for facts you previously saved about the user. Call
        this at the start of a conversation, or whenever answering well depends on something
        the user told you before (preferences, history, personal details). Returns the most
        relevant remembered facts, or a note that none were found.

        Args:
            query: What you want to recall, phrased as a search query.
                Example: "dietary restrictions and food preferences".
        """
        # retrieve_memories needs a FULLY RESOLVED namespace — wildcards are not supported
        # — so we substitute {actorId} ourselves.
        namespace = SEMANTIC_NAMESPACE.format(actorId=ACTOR_ID)
        records = memory_client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=5,
        )
        texts = []
        for record in records:
            # Record shape: {"content": {"text": "..."}, "score": 0.87, ...}
            content = record.get("content", {})
            text = content.get("text", "").strip() if isinstance(content, dict) else ""
            if text:
                texts.append(text)
        logger.info(f"🔎 recall_memory: {len(texts)} record(s) for query {query!r}")
        if not texts:
            return "No relevant memories found. This may be a new user, or extraction may still be processing."
        return "Relevant memories about the user:\n" + "\n".join(f"- {t}" for t in texts)

    return [store_memory, recall_memory]


# ## Step 3: Create the memory resource (built-in Semantic strategy)
#
# One memory with a single built-in **Semantic** strategy (no IAM role). We reuse it by
# name if it already exists.


def get_or_create_memory(name: str) -> str:
    """Create a memory with a built-in Semantic strategy, or reuse it if it exists."""
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "PersonalAssistantFacts",
                "description": "Captures standalone facts about the user from conversations",
                "namespaces": [SEMANTIC_NAMESPACE],
            }
        }
    ]
    # create_or_get_memory creates the memory, or returns the existing one (as a dict with
    # an "id" key) if a memory with this name already exists. The SDK handles the
    # name-clash lookup for us, so no manual error handling is needed.
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # strategies => long-term extraction is enabled
        description="LangGraph memory-as-tool LTM tutorial",
        event_expiry_days=7,  # retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with built-in Semantic strategy ready: {memory_id}")
    return memory_id


def wait_for_extraction(memory_id: str) -> None:
    """Poll until long-term records appear, or until the max wait elapses.

    In production you would NOT block like this — the agent simply calls recall_memory on
    the user's next visit. We block here only so the demo's second session has data to recall.
    """
    logger.info("⏳ Waiting for asynchronous Semantic extraction to complete...")
    namespace = SEMANTIC_NAMESPACE.format(actorId=ACTOR_ID)
    deadline = time.time() + EXTRACTION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            records = memory_client.retrieve_memories(
                memory_id=memory_id,
                namespace=namespace,
                query="user facts and details",
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


# ## Step 4: Run one user turn through the agent
#
# `create_agent(model, tools, system_prompt=...)` builds the ReAct loop: the model may
# call store_memory / recall_memory zero or more times before producing its final text.
# We pass the full message list each turn so the agent has the in-session history.


def run_turn(graph, messages: list, user_text: str) -> str:
    """Append the user's message, invoke the agent, return its final reply text.

    `messages` is the running conversation state (mutated in place) so multi-turn context
    is preserved within a session.
    """
    messages.append(HumanMessage(content=user_text))
    result = graph.invoke({"messages": messages})
    # create_agent returns the full message list (including any tool calls/results and the
    # final AI message). Sync our local history to it and return the last message's text.
    messages[:] = result["messages"]
    return messages[-1].text


# ## Step 5: Run the demo
#
# Two sessions, same ACTOR_ID:
#   • Session 1 (seed): the user shares durable facts; the agent should call store_memory.
#   • (wait for extraction)
#   • Session 2 (new): a FRESH message list — no in-session history. The user asks
#     something that depends on the past; the agent should call recall_memory itself.
#
# We never inject memory into the prompt and never call create_event/retrieve_memories
# directly in the demo flow — every memory operation happens because the MODEL asked.


def main() -> None:
    memory_id = None  # init so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("LangGraphMemoryAsTool")

        # Initialize the Bedrock-backed chat model.
        llm = init_chat_model(MODEL_ID, model_provider="bedrock_converse", region_name=REGION)

        # ---- Session 1: the agent saves facts as it learns them ------------------
        print("\n=== Session 1 (the agent decides what to store) ===")
        seed_tools = build_memory_tools(memory_id, SEED_SESSION_ID)
        seed_graph = create_agent(model=llm, tools=seed_tools, system_prompt=SYSTEM_PROMPT)
        seed_messages: list = []
        for user_text in [
            "Hi! I'm Sam. I just moved to Seattle and I'm trying to cook at home more.",
            "I should mention I'm vegetarian, and I'm allergic to shellfish.",
            "I'm also training for a half marathon in October, so I'm watching my protein intake.",
        ]:
            print(f"User:  {user_text}")
            reply = run_turn(seed_graph, seed_messages, user_text)
            print(f"Agent: {reply}\n")

        # ---- Wait for the extraction pipeline ------------------------------------
        wait_for_extraction(memory_id)

        # ---- Session 2: brand-new conversation, the agent recalls on its own -----
        # Fresh message list => no in-session history. The only way it can personalize is
        # to call recall_memory itself. The session_id differs, but the ACTOR_ID is the
        # same, so recall_memory reads the same /users/<actor>/facts/ namespace.
        print("=== Session 2 (new conversation — the agent decides what to recall) ===")
        new_tools = build_memory_tools(memory_id, NEW_SESSION_ID)
        new_graph = create_agent(model=llm, tools=new_tools, system_prompt=SYSTEM_PROMPT)
        new_messages: list = []
        for user_text in [
            "Hey, it's Sam again. Can you suggest a high-protein dinner I could make tonight?",
        ]:
            print(f"User:  {user_text}")
            reply = run_turn(new_graph, new_messages, user_text)
            print(f"Agent: {reply}\n")

    finally:
        # ## Cleanup
        #
        # AgentCore Memory resources are billable, so we delete the resource when the demo
        # finishes. To keep the memory between runs (e.g. to inspect it in the console),
        # comment out the block below — `get_or_create_memory` will reuse it.
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
