#!/usr/bin/env python

# # Claude SDK with AgentCore Memory (Long-Term Memory — Built-in Strategies)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **conversational agent** using the
# **Anthropic Claude SDK** (via Amazon Bedrock) with AgentCore **long-term memory**
# powered by **built-in strategies**. Where short-term memory stores raw conversation
# turns verbatim (see the 01-short-term-memory examples), long-term memory runs an
# asynchronous extraction pipeline over those turns and distills them into reusable,
# searchable records — standalone facts and stable user preferences.
#
# Because the Anthropic SDK is a **stateless API client** with NO built-in conversation
# management or hooks, the integration is fully explicit. We:
#   1. create a memory resource WITH strategies (SEMANTIC + USER_PREFERENCE),
#   2. drive a conversation, persisting each turn with `create_event` (the same call
#      used for short-term memory — adding strategies is what triggers extraction),
#   3. wait for the asynchronous extraction to run,
#   4. retrieve the distilled records with `retrieve_memories`, and
#   5. inject them into the system prompt of a *new* session so the agent recalls the
#      user across conversations — even with an empty `messages[]` array.
#
# **NOTE:** Built-in strategies do NOT require an IAM execution role. AgentCore Memory
# manages the extraction/consolidation models for you. To customize those models or
# prompts, see the strategy-override examples under `02-long-term-memory`.
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
# | Tutorial components | AgentCore Built-in Strategies (Semantic + User Preference), create_event, retrieve_memories |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create a memory resource with built-in long-term strategies (no IAM role required)
# - Call Claude through Amazon Bedrock using the `AnthropicBedrock` client
# - Store each turn with `create_event` to feed the extraction pipeline
# - Wait for asynchronous extraction, then read records back with `retrieve_memories`
# - Inject retrieved long-term memories into the system prompt for a future session
#
# ## Architecture
#
# ```
#   ┌──────────────┐                              ┌─────────────────────────────┐
#   │  Your code   │ ──── 1. create_event ──────▶ │  AgentCore Memory           │
#   │ (messages[]) │       (each turn)            │                             │
#   │              │                              │  short-term events ──┐      │
#   │              │                              │                      │      │
#   │              │                              │   2. async extraction│      │
#   │              │                              │      (built-in       ▼      │
#   │              │                              │       strategies) long-term │
#   │              │ ◀─── 3. retrieve_memories ── │   • Semantic facts   records│
#   │              │       (per namespace)        │   • User preferences        │
#   └──────┬───────┘                              └─────────────────────────────┘
#          │
#          │ 4. inject records into system prompt, then messages.create(...)
#          ▼
#   ┌──────────────┐
#   │ Claude via   │  5. assistant reply, now personalized from long-term memory
#   │ Amazon       │
#   │ Bedrock      │
#   └──────────────┘
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
logger = logging.getLogger("claude-sdk-ltm")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory
ACTOR_ID = "user_123"  # Any unique identifier for the end-user / agent
SESSION_ID = "personal_session_001"  # Unique identifier for the first conversation

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock.
# The `global.` prefix selects the global (cross-region) inference endpoint, which is
# the default for Sonnet 4.6 and carries no regional pricing premium. To pin traffic
# to a region instead, swap the prefix (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# Namespace templates tell AgentCore where each strategy's records are written. The
# `{actorId}` placeholder is substituted with the actor at extraction time, keeping
# every user's records isolated. We retrieve from the resolved path (see Step 5).
# These follow the built-in defaults: facts per-user, preferences per-user.
SEMANTIC_NAMESPACE = "/users/{actorId}/facts/"
PREFERENCE_NAMESPACE = "/users/{actorId}/preferences/"

# Long-term extraction is asynchronous — records appear ~30-90s after create_event.
# We poll for them (Step 6) but cap the total wait so the demo always finishes.
EXTRACTION_MAX_WAIT_SECONDS = 120
EXTRACTION_POLL_INTERVAL_SECONDS = 15

SYSTEM_PROMPT = (
    "You are a helpful personal assistant. Be friendly, concise, and professional. "
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


# ## Step 3: Create the Memory Resource (with built-in long-term strategies)
#
# This is the key difference from the short-term tutorial. There we passed
# `strategies=[]` (raw events only). Here we attach two built-in strategies:
#
# - **Semantic** — extracts standalone facts about the user/world (e.g. "User is
#   planning a trip to Japan"). Records land in the semantic namespace.
# - **User Preference** — extracts stable, durable preferences (e.g. "Prefers
#   vegetarian food"). Records land in the preferences namespace.
#
# Built-in strategies require NO IAM execution role — AgentCore manages the extraction
# and consolidation models. `create_or_get_memory` blocks until the resource is ACTIVE,
# and if a memory with this name already exists it returns the existing one instead of
# failing, so the demo is safe to re-run.


def get_or_create_memory(name: str) -> str:
    """Create a memory with built-in long-term strategies, or reuse it if it exists."""
    # Each strategy is a single-key dict: the key is the strategy type's wire value
    # (StrategyType.SEMANTIC.value == "semanticMemoryStrategy"), the value configures it.
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "PersonalAssistantFacts",
                "description": "Captures standalone facts about the user from conversations",
                "namespaceTemplates": [SEMANTIC_NAMESPACE],
            }
        },
        {
            StrategyType.USER_PREFERENCE.value: {
                "name": "PersonalAssistantPreferences",
                "description": "Captures stable user preferences across sessions",
                "namespaceTemplates": [PREFERENCE_NAMESPACE],
            }
        },
    ]

    # create_or_get_memory creates the resource if its name is free and otherwise
    # returns the existing memory dict, so we don't have to handle the name-clash case
    # ourselves. It blocks until the resource is ACTIVE.
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # Strategies => long-term extraction is enabled
        description="Long-term memory for the Claude SDK built-in-strategies tutorial",
        event_expiry_days=7,  # Retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with built-in strategies ready: {memory_id}")
    return memory_id


# ## Step 4: The conversation turn
#
# Each turn mirrors the short-term tutorial: append the user message, call Claude with
# the full local history, append the reply, then persist the exchange with
# `create_event`. The ONLY difference is that, because this memory has strategies
# attached, every stored event is *also* fed into the long-term extraction pipeline.
# We don't call a special "extract" API — `create_event` is the trigger.


def extract_text(response) -> str:
    """Concatenate all text blocks from a Claude response.

    A Messages API response `.content` is a list of content blocks; we only want the
    text blocks (this simple assistant defines no tools, so there are no tool_use
    blocks to handle here).
    """
    return "".join(block.text for block in response.content if block.type == "text")


def chat_turn(memory_id: str, messages: list, user_text: str, system_prompt: str) -> str:
    """Run a single conversation turn end-to-end and return the assistant's reply."""
    # 1. Append the user's message to the local conversation state.
    messages.append({"role": "user", "content": user_text})

    # 2. Call Claude with the full conversation history and (possibly enriched) prompt.
    response = claude.messages.create(
        model=MODEL_ID,
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )

    # 3. Extract the reply and append it so the next turn has full context.
    assistant_text = extract_text(response)
    messages.append({"role": "assistant", "content": assistant_text})

    # 4. Persist this exchange. Because the memory has strategies, this event will be
    #    asynchronously processed into long-term semantic/preference records.
    try:
        memory_client.create_event(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=SESSION_ID,
            messages=[
                (user_text, "USER"),
                (assistant_text, "ASSISTANT"),
            ],
        )
        logger.info("✅ Stored turn (queued for long-term extraction)")
    except Exception as e:
        # Don't crash the conversation if a single write fails; log and continue.
        logger.error(f"Memory save error: {e}")

    return assistant_text


# ## Step 5: Retrieve long-term memories
#
# `retrieve_memories` runs a semantic search over a namespace and returns the most
# relevant records. The namespace must be FULLY RESOLVED — wildcards are not supported,
# so we substitute `{actorId}` ourselves. Each returned record is a dict whose
# `content.text` holds the extracted memory and `score` holds the relevance.


def retrieve_long_term(memory_id: str, namespace_template: str, query: str, top_k: int = 5) -> list:
    """Retrieve extracted long-term records from one resolved namespace."""
    namespace = namespace_template.format(actorId=ACTOR_ID)
    try:
        records = memory_client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=top_k,
        )
    except Exception as e:
        logger.error(f"Failed to retrieve memories from {namespace}: {e}")
        return []

    texts = []
    for record in records:
        # Record shape: {"content": {"text": "..."}, "score": 0.87, ...}
        content = record.get("content", {})
        text = content.get("text", "").strip() if isinstance(content, dict) else ""
        if text:
            texts.append(text)
    logger.info(f"✅ Retrieved {len(texts)} record(s) from {namespace}")
    return texts


def build_memory_enriched_prompt(memory_id: str, query: str) -> str:
    """Fetch long-term records and fold them into a system prompt for a new session.

    This is how a stateless Claude agent gets 'memory' across conversations: we pull
    the distilled facts/preferences and prepend them as context, so even a brand-new
    `messages[]` array carries what the agent learned previously.
    """
    facts = retrieve_long_term(memory_id, SEMANTIC_NAMESPACE, query)
    preferences = retrieve_long_term(memory_id, PREFERENCE_NAMESPACE, query)

    if not facts and not preferences:
        # Nothing extracted yet — fall back to the base prompt.
        return SYSTEM_PROMPT

    context_lines = ["", "What you remember about this user from previous conversations:"]
    for fact in facts:
        context_lines.append(f"- (fact) {fact}")
    for preference in preferences:
        context_lines.append(f"- (preference) {preference}")
    context_lines.append("Use this context to personalize your responses when relevant.")

    return SYSTEM_PROMPT + "\n".join(context_lines)


# ## Step 6: Wait for extraction
#
# Long-term extraction is asynchronous — records do not exist the instant
# `create_event` returns. We poll the semantic namespace until records appear or we hit
# the cap. In production you would not block like this; you'd retrieve on the next user
# interaction (typically minutes later) by which point extraction has long completed.


def wait_for_extraction(memory_id: str) -> None:
    """Poll until long-term records appear, or until the max wait elapses."""
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


# ## Step 7: Run the demo
#
# We run a first conversation (which seeds memory), wait for extraction, then start a
# SECOND session with a fresh `messages[]` array. The second session's system prompt is
# enriched with the long-term records retrieved from the first — so the agent recalls
# the user despite having no short-term history loaded.


def main() -> None:
    memory_id = None  # Initialize so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("ClaudeSDKLongTermMemory")

        # ---- First conversation: seed long-term memory --------------------------
        # A fresh conversation. Each turn is stored and queued for extraction.
        print("\n=== First Conversation (seeding long-term memory) ===")
        messages: list = []
        for user_text in [
            "Hi! My name is Alex and I'm planning a trip to Japan in the spring.",
            "I'm vegetarian, and I really prefer quiet, off-the-beaten-path places over crowds.",
            "I also love photography — especially temples and gardens.",
        ]:
            print(f"User:  {user_text}")
            reply = chat_turn(memory_id, messages, user_text, SYSTEM_PROMPT)
            print(f"Agent: {reply}\n")

        # ---- Wait for the extraction pipeline -----------------------------------
        wait_for_extraction(memory_id)

        # ---- Inspect the extracted records --------------------------------------
        print("=== Extracted Long-Term Memory ===")
        print("Semantic facts:")
        for fact in retrieve_long_term(memory_id, SEMANTIC_NAMESPACE, "trip plans and personal details"):
            print(f"  • {fact}")
        print("User preferences:")
        for pref in retrieve_long_term(memory_id, PREFERENCE_NAMESPACE, "travel and food preferences"):
            print(f"  • {pref}")
        print()

        # ---- Second session: new state, memory injected into the prompt ---------
        # Simulate the user returning later. We start with an EMPTY messages[] array
        # (no short-term history) and instead enrich the system prompt with the
        # long-term records retrieved above. If extraction worked, the agent still
        # knows the user's name, dietary needs, and interests.
        print("=== Second Session (new process, long-term memory injected) ===")
        follow_up = "Can you suggest an activity for my trip?"
        enriched_prompt = build_memory_enriched_prompt(memory_id, query=follow_up)
        new_messages: list = []  # No short-term history — memory comes from the prompt.

        print(f"User:  {follow_up}")
        reply = chat_turn(memory_id, new_messages, follow_up, enriched_prompt)
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
