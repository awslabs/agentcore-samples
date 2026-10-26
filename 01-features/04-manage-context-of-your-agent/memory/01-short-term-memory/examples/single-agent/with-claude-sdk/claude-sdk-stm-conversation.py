#!/usr/bin/env python

# # Claude SDK with AgentCore Memory (Short-Term Memory)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **conversational agent** using the
# **Anthropic Claude SDK** (via Amazon Bedrock) with AgentCore **short-term memory**
# (raw events). Unlike higher-level frameworks (Strands, LangGraph, LlamaIndex),
# the Anthropic SDK is a **stateless API client** — it has NO built-in conversation
# management. We are responsible for maintaining the `messages[]` array and for
# wiring memory in and out of it ourselves.
#
# That makes this the clearest example of *what AgentCore Memory actually does for
# you*: we manually store each turn with `create_event` and rehydrate the
# conversation with `get_last_k_turns` so a brand-new process can resume exactly
# where the previous one left off.
#
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Short Term Conversational                                                        |
# | Agent type          | Personal Assistant                                                               |
# | Agentic Framework   | Anthropic Claude SDK (no framework)                                              |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Tutorial components | AgentCore Short-term Memory, manual messages[] + agentic loop                    |
# | Example complexity  | Beginner                                                                         |
#
# You'll learn to:
# - Call Claude through Amazon Bedrock using the `AnthropicBedrock` client
# - Use short-term memory for conversation continuity
# - Store each user/assistant turn as an event with `create_event`
# - Rehydrate conversation history with `get_last_k_turns` to resume across processes
# - Manage the `messages[]` array yourself (the SDK does not)
#
# ## Architecture
#
# ```
#   ┌──────────────┐   1. rehydrate history    ┌────────────────────────┐
#   │  Your code   │ ◀──── get_last_k_turns ────│  AgentCore Memory      │
#   │ (messages[]) │                            │  (short-term / events) │
#   │              │ ───── create_event ──────▶ │                        │
#   └──────┬───────┘   4. store each turn       └────────────────────────┘
#          │
#          │ 2. messages.create(messages=[...])
#          ▼
#   ┌──────────────┐
#   │ Claude via   │  3. assistant reply
#   │ Amazon       │ ─────────────────────────┐
#   │ Bedrock      │                           │
#   └──────────────┘                           ▼
#                                       (appended to messages[]
#                                        and stored back to Memory)
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
from datetime import datetime

# The Anthropic SDK's Amazon Bedrock client. `pip install "anthropic[bedrock]"`
# provides this; it signs requests with your AWS credentials (SigV4) and speaks the
# Messages API against Bedrock — no Anthropic API key required.
from anthropic import AnthropicBedrock

# AgentCore Memory client.
from bedrock_agentcore.memory import MemoryClient

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("claude-sdk-stm")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory
ACTOR_ID = "user_123"  # Any unique identifier for the end-user / agent
SESSION_ID = "personal_session_001"  # Unique identifier for this conversation

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock.
# The `global.` prefix selects the global (cross-region) inference endpoint, which is
# the default for Sonnet 4.6 and carries no regional pricing premium. To pin traffic
# to a region instead, swap the prefix (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# How many turns of history to rehydrate when resuming a conversation.
HISTORY_TURNS = 5

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


# ## Step 3: Create the Memory Resource (short-term only)
#
# For short-term memory we create a memory resource with NO strategies. This stores
# raw conversation turns ("events") that we read back with `get_last_k_turns`.
# Long-term strategies (semantic/summary/user-preference extraction) are covered in
# the 02-long-term-memory examples.
#
# `create_or_get_memory` blocks until the resource is ACTIVE (typically a few seconds
# for a strategy-less memory) and, if a memory with this name already exists (e.g. from
# a previous run), looks it up and reuses it instead of failing.


def get_or_create_memory(name: str) -> str:
    """Create a strategy-less (short-term) memory, or reuse it if it already exists."""
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=[],  # No strategies => short-term (raw events) only
        description="Short-term memory for the Claude SDK conversation tutorial",
        event_expiry_days=7,  # Retain raw events for 7 days (configurable 3-365)
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory ready: {memory_id}")
    return memory_id


# ## Step 4: Memory integration helpers
#
# Because the Anthropic SDK is stateless, memory integration is just two functions:
#
# 1. `load_history()` — rehydrate the Anthropic `messages[]` array from AgentCore
#    short-term memory at startup (the RETRIEVE step).
# 2. `store_turn()` — persist a completed user/assistant exchange back to memory
#    after each turn (the STORE step).


def load_history(memory_id: str) -> list:
    """Rehydrate the Anthropic messages[] array from AgentCore short-term memory.

    `get_last_k_turns` returns the most recent k turns, oldest-first, as a
    list-of-turns where each turn is a list of message dicts shaped like:
        {"role": "USER" | "ASSISTANT" | "TOOL", "content": {"text": "..."}}
    We translate those into the {"role": ..., "content": ...} dicts the
    Anthropic Messages API expects.
    """
    messages: list = []
    try:
        recent_turns = memory_client.get_last_k_turns(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=SESSION_ID,
            k=HISTORY_TURNS,
        )
    except Exception as e:
        # A missing/empty session is normal on the very first run — start fresh.
        logger.warning(f"Could not load history (starting fresh): {e}")
        return messages

    for turn in recent_turns:
        for message in turn:
            role = message["role"].lower()  # AgentCore stores USER/ASSISTANT; API wants lowercase
            text = message["content"]["text"]
            # The Anthropic Messages API only accepts the "user" and "assistant"
            # roles in messages[]; skip TOOL or any other stored role here.
            if role in ("user", "assistant"):
                messages.append({"role": role, "content": text})

    if messages:
        logger.info(f"✅ Rehydrated {len(messages)} message(s) from {len(recent_turns)} turn(s)")
    else:
        logger.info("No prior history found — starting a new conversation")
    return messages


def store_turn(memory_id: str, user_text: str, assistant_text: str) -> None:
    """Persist one completed user/assistant exchange to AgentCore short-term memory.

    `create_event` takes the messages as a list of (text, role) tuples. Roles must be
    one of USER, ASSISTANT, or TOOL. We store the pair as a single event so the turn
    is retrieved together by `get_last_k_turns`.
    """
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
        logger.info("✅ Stored turn to short-term memory")
    except Exception as e:
        # Don't crash the conversation if a single write fails; log and continue.
        logger.error(f"Memory save error: {e}")


# ## Step 5: The conversation turn
#
# This is the core integration point. For each user message we:
#   1. Append the user message to the local messages[] array.
#   2. Call Claude via Bedrock with the full history + system prompt.
#   3. Extract the assistant's text reply and append it to messages[].
#   4. Persist the completed turn back to AgentCore short-term memory.


def extract_text(response) -> str:
    """Concatenate all text blocks from a Claude response.

    A Messages API response `.content` is a list of content blocks; we only want the
    text blocks (this simple assistant defines no tools, so there are no tool_use
    blocks to handle here).
    """
    return "".join(block.text for block in response.content if block.type == "text")


def chat_turn(memory_id: str, messages: list, user_text: str) -> str:
    """Run a single conversation turn end-to-end and return the assistant's reply."""
    # 1. Append the user's message to the local conversation state.
    messages.append({"role": "user", "content": user_text})

    # 2. Call Claude with the full conversation history.
    response = claude.messages.create(
        model=MODEL_ID,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    # 3. Extract the reply and append it so the next turn has full context.
    assistant_text = extract_text(response)
    messages.append({"role": "assistant", "content": assistant_text})

    # 4. Persist this exchange so a future process can resume the conversation.
    store_turn(memory_id, user_text, assistant_text)

    return assistant_text


# ## Step 6: Run the demo
#
# We run two separate "sessions" against the SAME AgentCore session_id to prove
# continuity: the second agent is a fresh object with an empty messages[] array, yet
# it answers correctly because it rehydrated the first conversation from memory.


def main() -> None:
    memory_id = None  # Initialize so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("ClaudeSDKShortTermMemory")

        # ---- First conversation -------------------------------------------------
        # A fresh conversation: load_history() finds nothing, so messages[] starts empty.
        print("\n=== First Conversation ===")
        messages = load_history(memory_id)

        for user_text in [
            "My name is Alex and I'm planning a trip to Japan.",
            "I'm especially interested in food experiences.",
        ]:
            print(f"User:  {user_text}")
            reply = chat_turn(memory_id, messages, user_text)
            print(f"Agent: {reply}\n")

        # ---- User returns: brand-new agent state --------------------------------
        # Simulate the user coming back later in a NEW process. We deliberately throw
        # away the in-memory `messages` list and rebuild it ONLY from AgentCore
        # short-term memory. If continuity works, the agent still knows the user's
        # name and interests.
        print("=== User Returns (new process, history rehydrated from memory) ===")
        resumed_messages = load_history(memory_id)

        for user_text in [
            "What was my name again, and where am I going?",
            "Suggest one thing to do based on what I told you.",
        ]:
            print(f"User:  {user_text}")
            reply = chat_turn(memory_id, resumed_messages, user_text)
            print(f"Agent: {reply}\n")

        # ---- Inspect what's stored ---------------------------------------------
        print("=== Stored Short-Term Memory (last 3 turns) ===")
        for i, turn in enumerate(
            memory_client.get_last_k_turns(
                memory_id=memory_id,
                actor_id=ACTOR_ID,
                session_id=SESSION_ID,
                k=3,
            ),
            start=1,
        ):
            print(f"Turn {i}:")
            for message in turn:
                role = message["role"]
                text = message["content"]["text"]
                preview = text[:100] + "..." if len(text) > 100 else text
                print(f"  {role}: {preview}")
            print()

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
