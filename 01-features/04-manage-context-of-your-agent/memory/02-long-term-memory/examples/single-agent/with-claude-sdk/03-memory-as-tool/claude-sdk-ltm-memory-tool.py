#!/usr/bin/env python

# # Claude SDK with AgentCore Memory (Long-Term Memory — Memory as a Tool)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **conversational agent** using the
# **Anthropic Claude SDK** (via Amazon Bedrock) with AgentCore **long-term memory**
# exposed as **tools the model decides to call**. The two companion tutorials wired
# memory in *around* the model: `01-built-in-strategies` stored every turn and
# injected retrieved records into the system prompt; `02-custom-strategy-override`
# did the same with a customized extraction pipeline. In both, *your code* decided
# when to save and when to recall.
#
# Here we hand that decision to the agent. We define two tools —
# `store_memory` and `recall_memory` — and pass them to Claude via the `tools=`
# parameter. Claude chooses, on its own, when to persist a durable fact and when to
# search its past knowledge, by emitting `tool_use` blocks. We run the standard
# **agentic loop** (call the model → execute the tools it requested → feed the
# results back → repeat until it stops asking for tools), and the tool
# implementations call AgentCore Memory directly: `create_event` to store,
# `retrieve_memories` to recall.
#
# Because the Anthropic SDK is a **stateless API client** with NO built-in
# conversation management or hooks, the entire loop is explicit. That makes this the
# clearest possible view of the memory-as-tool pattern: there is no framework
# deciding anything for you — the model asks, your loop dispatches, AgentCore stores
# and retrieves.
#
# **NOTE:** We use a built-in **Semantic** strategy, so `store_memory`'s `create_event`
# call feeds the same asynchronous extraction pipeline used in tutorial 01, and
# `recall_memory`'s `retrieve_memories` reads the distilled records back. Built-in
# strategies require NO IAM execution role. Extraction is asynchronous (~30-90s), so a
# fact saved in one turn is not instantly searchable in the next — we therefore seed
# memory in a first session, wait for extraction, then show a *new* session where the
# agent recalls across the gap (exactly how it behaves in production).
#
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                         |
# | Agent type          | Personal Assistant                                                               |
# | Agentic Framework   | Anthropic Claude SDK (no framework)                                              |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Tutorial components | AgentCore Built-in Semantic strategy, tool use (store_memory + recall_memory), create_event, retrieve_memories |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Define memory operations (`store_memory`, `recall_memory`) as Claude tools via `tools=`
# - Run the agentic loop (tool_use → tool_result → continue) per Anthropic's spec
# - Back those tools with AgentCore Memory: `create_event` to store, `retrieve_memories` to recall
# - Let the model decide WHEN to save a durable fact and WHEN to search its past knowledge
# - Show cross-session recall: a fresh session with empty `messages[]` where the agent
#   recovers context entirely by calling `recall_memory` itself
#
# ## Memory-as-tool vs. the other two patterns
#
# | | 01 — Built-in (post-response) | 02 — Custom override | 03 — Memory as a tool (this) |
# |---|---|---|---|
# | **Who decides to store** | Your code, after every turn | Your code, after every turn | **The model**, via `store_memory` |
# | **Who decides to recall** | Your code, at session start | Your code, at session start | **The model**, via `recall_memory` |
# | **How memory reaches the model** | Injected into the system prompt | Injected into the system prompt | Returned as a `tool_result` the model requested |
# | **Control flow** | Linear (store → retrieve → prompt) | Linear | **Agentic loop** (model drives) |
# | **Best when** | You want deterministic, always-on memory | …plus customized extraction | The model should manage its own memory lifecycle |
#
# ## Architecture
#
# ```
#   ┌──────────────────────────────────────────────────────────────────────────┐
#   │  Agentic loop (your code)                                                  │
#   │                                                                            │
#   │   messages.create(tools=[store_memory, recall_memory]) ──▶ Claude (Bedrock)│
#   │                          ▲                                      │          │
#   │                          │                                      ▼          │
#   │             tool_result  │                            stop_reason ==       │
#   │             (USER turn)  │                              "tool_use"?        │
#   │                          │                                      │ yes      │
#   │                          │                                      ▼          │
#   │                          │                       ┌──────────────────────┐  │
#   │                          └───────────────────────┤ dispatch tool_use:   │  │
#   │                                                  │  • store_memory      │  │
#   │                                                  │  • recall_memory     │  │
#   │                                                  └──────────┬───────────┘  │
#   └─────────────────────────────────────────────────────────────┼────────────┘
#                                                                   │
#              store_memory → create_event                          │
#              recall_memory → retrieve_memories                    ▼
#   ┌────────────────────────────────────────────────────────────────────────┐
#   │  AgentCore Memory                                                        │
#   │   create_event ──▶ short-term events ──▶ async Semantic extraction ──▶   │
#   │   long-term records ◀── retrieve_memories (semantic search by namespace) │
#   └────────────────────────────────────────────────────────────────────────┘
# ```
#
# ## Prerequisites
#
# To execute this tutorial you will need:
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - Access to the Claude Sonnet 4.6 model in Amazon Bedrock (request it in the
#   Bedrock console under Model access, in your chosen region)
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import logging
import os
import time
from datetime import datetime

# The Anthropic SDK's Amazon Bedrock client. `pip install "anthropic[bedrock]"`
# provides this; it signs requests with your AWS credentials (SigV4) and speaks the
# Messages API against Bedrock — no Anthropic API key required.
from anthropic import AnthropicBedrock

# AgentCore Memory client and the StrategyType enum (gives us the exact strategy keys).
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("claude-sdk-ltm-tool")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory
ACTOR_ID = "user_789"  # Any unique identifier for the end-user / agent

# Two distinct sessions: the first seeds memory, the second (a "new conversation")
# recalls across the gap. Both share the same ACTOR_ID — that's what ties a user's
# long-term records together across sessions.
SEED_SESSION_ID = "assistant_session_001"
NEW_SESSION_ID = "assistant_session_002"

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock.
# The `global.` prefix selects the global (cross-region) inference endpoint, which is
# the default for Sonnet 4.6 and carries no regional pricing premium. To pin traffic
# to a region instead, swap the prefix (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# Namespace template for the semantic records. `{actorId}` is substituted at extraction
# time, keeping every user's facts isolated. `recall_memory` retrieves from the resolved
# path (see Step 3). This follows the built-in default: facts per-user.
SEMANTIC_NAMESPACE = "/users/{actorId}/facts/"

# Long-term extraction is asynchronous — records appear ~30-90s after create_event.
# We poll for them (Step 5) but cap the total wait so the demo always finishes.
EXTRACTION_MAX_WAIT_SECONDS = 120
EXTRACTION_POLL_INTERVAL_SECONDS = 15

# Safety bound on the agentic loop. A well-behaved model finishes in 1-3 iterations;
# the cap guarantees we never spin forever if it keeps requesting tools.
MAX_TOOL_ITERATIONS = 6

# The system prompt is where we tell the agent it HAS memory tools and WHEN to use
# them. With the memory-as-tool pattern, this prompt is doing the steering that
# tutorials 01/02 did in code — be explicit so the model reaches for the tools.
SYSTEM_PROMPT = (
    "You are a helpful personal assistant with persistent long-term memory.\n"
    "\n"
    "You have two memory tools:\n"
    "- store_memory: save a durable fact about the user so you can recall it in future "
    "conversations. Call it whenever the user shares something worth remembering long-term "
    "— their name, preferences, goals, constraints, important dates. Do NOT store transient "
    "small talk or things that won't matter later.\n"
    "- recall_memory: search your long-term memory for facts you saved previously. Call it "
    "at the START of a conversation, and any time answering well depends on something the "
    "user may have told you before.\n"
    "\n"
    "Use these tools proactively and on your own judgment — the user will not remind you. "
    "Be friendly, concise, and professional. "
    f"Today's date: {datetime.today().strftime('%Y-%m-%d')}."
)


# ## Step 2: Initialize the Claude (Bedrock) and Memory clients
#
# The `AnthropicBedrock` client resolves AWS credentials the same way boto3 does
# (environment variables, shared `~/.aws/credentials`, or an instance/role profile).
# We only need to tell it which region to call.


claude = AnthropicBedrock(aws_region=REGION)
memory_client = MemoryClient(region_name=REGION)
logger.info(f"✅ Clients initialized for region: {REGION}")


# ## Step 3: Define the memory tools (schemas + implementations)
#
# A tool is two things: a JSON-Schema declaration the model sees (name, description,
# input shape) and a Python function that runs when the model calls it. The model only
# ever sees the declarations; it emits a `tool_use` block naming a tool and supplying
# arguments, and our loop (Step 4) routes that to the matching implementation.
#
# The descriptions are load-bearing — Claude decides WHETHER and WHEN to call a tool
# almost entirely from its description, so we state the trigger conditions explicitly.

TOOLS = [
    {
        "name": "store_memory",
        "description": (
            "Save an important, durable fact about the user to long-term memory so it can "
            "be recalled in future conversations. Call this whenever the user shares "
            "information worth remembering across sessions — their name, preferences, goals, "
            "constraints, or notable personal details. Do NOT call it for transient small "
            "talk or anything that won't matter later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "A concise, self-contained statement of the fact to remember, written "
                        "in the third person. Example: 'The user is vegetarian and allergic to "
                        "shellfish.'"
                    ),
                }
            },
            "required": ["fact"],
        },
    },
    {
        "name": "recall_memory",
        "description": (
            "Search your long-term memory for facts you previously saved about the user. Call "
            "this at the start of a conversation, or whenever answering well depends on "
            "something the user told you before (preferences, history, personal details). "
            "Returns the most relevant remembered facts, or a note that none were found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What you want to recall, phrased as a search query. "
                        "Example: 'dietary restrictions and food preferences'."
                    ),
                }
            },
            "required": ["query"],
        },
    },
]


def tool_store_memory(memory_id: str, session_id: str, fact: str) -> str:
    """Implementation of the `store_memory` tool: persist a fact via create_event.

    We write the fact as a single conversational USER message. Because the memory
    resource has a Semantic strategy attached, this event feeds the same asynchronous
    extraction pipeline used in tutorial 01 — `create_event` is the trigger; there is
    no separate "extract" API. Raises are caught by the dispatcher (Step 4), which
    reports them back to the model as an error tool_result.
    """
    memory_client.create_event(
        memory_id=memory_id,
        actor_id=ACTOR_ID,
        session_id=session_id,
        messages=[(fact, "USER")],
    )
    logger.info(f"🧠 store_memory: queued for extraction — {fact!r}")
    return f"Saved to long-term memory: {fact}"


def tool_recall_memory(memory_id: str, query: str, top_k: int = 5) -> str:
    """Implementation of the `recall_memory` tool: semantic search via retrieve_memories.

    `retrieve_memories` searches a namespace and returns the most relevant extracted
    records. The namespace must be FULLY RESOLVED — wildcards are not supported — so we
    substitute `{actorId}` ourselves. Each record is a dict whose `content.text` holds
    the extracted memory and `score` holds the relevance. We return a compact,
    model-readable string (the tool_result content).
    """
    namespace = SEMANTIC_NAMESPACE.format(actorId=ACTOR_ID)
    records = memory_client.retrieve_memories(
        memory_id=memory_id,
        namespace=namespace,
        query=query,
        top_k=top_k,
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


def dispatch_tool(name: str, tool_input: dict, memory_id: str, session_id: str) -> tuple:
    """Route one tool_use block to its implementation.

    Returns (content, is_error). We never let a tool exception crash the loop — instead
    we hand the error text back to the model as an error tool_result so it can adapt
    (retry, ask the user, or proceed without the tool), exactly as Anthropic recommends.
    """
    try:
        if name == "store_memory":
            return tool_store_memory(memory_id, session_id, tool_input["fact"]), False
        if name == "recall_memory":
            return tool_recall_memory(memory_id, tool_input["query"]), False
        # The model invented a tool we don't expose — tell it so.
        return f"Unknown tool: {name}", True
    except Exception as e:
        logger.error(f"Tool '{name}' failed: {e}")
        return f"Error running {name}: {e}", True


# ## Step 4: The agentic loop
#
# This is the heart of the memory-as-tool pattern, and it follows Anthropic's tool-use
# spec exactly:
#
#   1. Call `messages.create(...)` with the `tools=` list.
#   2. Append the assistant's FULL `response.content` to `messages` — this preserves the
#      `tool_use` blocks the next request needs.
#   3. If `stop_reason != "tool_use"`, the model is done — return its text.
#   4. Otherwise, execute every `tool_use` block and append ONE `tool_result` per block
#      (matched by `tool_use_id`) as a single USER message.
#   5. Loop. The model now sees the results and continues (it may call more tools, or
#      produce a final answer).
#
# We bound the loop with MAX_TOOL_ITERATIONS as a guardrail.


def extract_text(response) -> str:
    """Concatenate all text blocks from a Claude response.

    A Messages API response `.content` is a list of content blocks; here we want only
    the text blocks (the model may also emit `tool_use` blocks, which the loop handles
    separately).
    """
    return "".join(block.text for block in response.content if block.type == "text")


def run_agent(memory_id: str, session_id: str, messages: list, user_text: str) -> str:
    """Run one user turn through the agentic loop and return the assistant's final reply.

    `messages` is the running conversation state (mutated in place). The model may call
    store_memory / recall_memory zero or more times before producing its final text.
    """
    # Append the user's message to the local conversation state.
    messages.append({"role": "user", "content": user_text})

    final_text = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        # 1. Call Claude with the full history and the tool declarations.
        response = claude.messages.create(
            model=MODEL_ID,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # 2. Append the assistant's full content (text + any tool_use blocks). Passing
        #    response.content back verbatim preserves the tool_use blocks the API needs
        #    to match our tool_result blocks on the next request.
        messages.append({"role": "assistant", "content": response.content})

        # 3. No tool calls => the model is done. Capture its text and stop.
        if response.stop_reason != "tool_use":
            final_text = extract_text(response)
            break

        # 4. Execute every tool_use block and collect one tool_result per block. The
        #    API requires a matching tool_result (by tool_use_id) for each tool_use.
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                content, is_error = dispatch_tool(block.name, block.input, memory_id, session_id)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,  # Must match the tool_use block's id
                        "content": content,
                        "is_error": is_error,
                    }
                )

        # 5. Feed the results back as a single USER turn, then loop so the model can
        #    continue with the tool output in hand.
        messages.append({"role": "user", "content": tool_results})
    else:
        # The loop hit MAX_TOOL_ITERATIONS without a final answer. Surface whatever text
        # the last response carried so the caller still gets something.
        logger.warning("⚠️ Hit MAX_TOOL_ITERATIONS without a final answer.")
        final_text = final_text or "(stopped: tool-use iteration limit reached)"

    return final_text


# ## Step 5: Create the memory resource, and wait-for-extraction helper
#
# We create a memory with a single built-in **Semantic** strategy (no IAM role
# required) via `create_or_get_memory`, which returns the existing memory by name if it
# already exists. The wait helper polls the semantic namespace until the records
# `store_memory` queued have been extracted, so the second session's `recall_memory` has
# something to find.


def get_or_create_memory(name: str) -> str:
    """Create a memory with a built-in Semantic strategy, or reuse it if it exists."""
    # A single-key dict keyed by the strategy type's wire value
    # (StrategyType.SEMANTIC.value == "semanticMemoryStrategy").
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "PersonalAssistantFacts",
                "description": "Captures standalone facts about the user from conversations",
                "namespaceTemplates": [SEMANTIC_NAMESPACE],
            }
        }
    ]

    # `create_or_get_memory` creates the resource, or returns the existing one if a
    # memory with this name already exists — so we don't hand-roll the "already exists"
    # path ourselves. Either way we get the memory dict back, keyed by "id".
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # Strategies => long-term extraction is enabled
        description="Long-term memory for the Claude SDK memory-as-tool tutorial",
        event_expiry_days=7,  # Retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with built-in Semantic strategy ready: {memory_id}")
    return memory_id


def wait_for_extraction(memory_id: str) -> None:
    """Poll until long-term records appear, or until the max wait elapses.

    In production you would NOT block like this — the agent simply calls recall_memory on
    the user's next visit (minutes or days later), by which point extraction has long
    completed. We block here only so the demo's second session has data to recall.
    """
    logger.info("⏳ Waiting for asynchronous long-term extraction to complete...")
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
        except Exception as e:
            logger.warning(f"Retrieval probe failed (will retry): {e}")
        time.sleep(EXTRACTION_POLL_INTERVAL_SECONDS)
    logger.warning(
        "⚠️ Extraction did not surface records within the wait window. "
        "It may still complete shortly — try re-running retrieval later."
    )


# ## Step 6: Run the demo
#
# Two sessions, same ACTOR_ID:
#
#   • Session 1 (seed): a multi-turn conversation where the user shares durable facts.
#     The agent should call `store_memory` on its own as those facts come up.
#   • (wait for extraction)
#   • Session 2 (new): a FRESH `messages[]` array — no short-term history at all. The
#     user asks something that depends on the past. The agent should call `recall_memory`
#     itself to recover the context, then answer.
#
# We never inject memory into the prompt and never call create_event/retrieve_memories
# directly in the demo flow — every memory operation happens because the MODEL asked.


def main() -> None:
    memory_id = None  # Initialize so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("ClaudeSDKMemoryAsTool")

        # ---- Session 1: the agent saves facts as it learns them -----------------
        print("\n=== Session 1 (the agent decides what to store) ===")
        seed_messages: list = []
        for user_text in [
            "Hi! I'm Sam. I just moved to Seattle and I'm trying to cook at home more.",
            "I should mention I'm vegetarian, and I'm allergic to shellfish.",
            "I'm also training for a half marathon in October, so I'm watching my protein intake.",
        ]:
            print(f"User:  {user_text}")
            reply = run_agent(memory_id, SEED_SESSION_ID, seed_messages, user_text)
            print(f"Agent: {reply}\n")

        # ---- Wait for the extraction pipeline -----------------------------------
        wait_for_extraction(memory_id)

        # ---- Session 2: brand-new conversation, the agent recalls on its own ----
        # Empty messages[] => the agent has NO short-term history. The only way it can
        # personalize its answer is to call recall_memory itself.
        print("=== Session 2 (new conversation — the agent decides what to recall) ===")
        new_messages: list = []
        for user_text in [
            "Hey, it's Sam again. Can you suggest a high-protein dinner I could make tonight?",
        ]:
            print(f"User:  {user_text}")
            reply = run_agent(memory_id, NEW_SESSION_ID, new_messages, user_text)
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
            except Exception as e:
                logger.error(f"Failed to delete memory {memory_id}: {e}")


if __name__ == "__main__":
    main()
