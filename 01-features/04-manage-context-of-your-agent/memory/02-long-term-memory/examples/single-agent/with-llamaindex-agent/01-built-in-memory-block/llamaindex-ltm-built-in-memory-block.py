#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Built-in Memory Block (Long-term Memory)
#
# ## Introduction
#
# This tutorial demonstrates the **idiomatic** way to give a LlamaIndex agent long-term
# memory backed by Amazon Bedrock AgentCore: a custom **memory block** wired into
# LlamaIndex's **`Memory`** class. Instead of exposing memory as a tool the LLM has to
# remember to call (see `../03-memory-tool/`), the block plugs into the agent's memory
# **lifecycle** — the framework calls it automatically at the right points:
#
#   * On **retrieval** (every turn) the block runs a semantic search against AgentCore
#     long-term memory and injects the results into the agent's context.
#   * On **flush** (when short-term history exceeds its token budget) the framework hands
#     the ejected messages to the block, which writes them to AgentCore as an event; the
#     memory's Semantic strategy then extracts durable records from them.
#
# > ### ⚠️ LlamaIndex API note — `Memory` + `BaseMemoryBlock` (NOT `ChatMemoryBuffer`)
# > `ChatMemoryBuffer` is **deprecated**. The current integration point is the
# > **`Memory`** class (`llama_index.core.memory.Memory`) composed of one or more
# > **`BaseMemoryBlock`** subclasses. A memory block implements two async hooks —
# > `_aget` (retrieve, every turn) and `_aput` (persist, on short-term flush) — plus an
# > optional `atruncate`. This file subclasses `BaseMemoryBlock[str]`; verified against
# > `llama-index-core` 0.14.x.
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term, single-agent                                                          |
# | Agent usecase       | Personal Knowledge Assistant (remembers facts about the user across sessions)    |
# | Agentic Framework   | LlamaIndex (`Memory` + `BaseMemoryBlock`, `FunctionAgent`)                        |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                  |
# | Strategies          | Semantic (facts) — **built-in** (no IAM execution role)                          |
# | Memory components    | `BaseMemoryBlock` wrapping `create_event` (put) + `search_long_term_memories` (get) |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Subclass `BaseMemoryBlock` to wrap AgentCore Memory (retrieve on get, persist on put)
# - Compose it into the LlamaIndex `Memory` class and hand that to a `FunctionAgent`
# - Let the framework drive persistence/retrieval instead of calling memory yourself
# - Keep the write namespace and the read namespace identical (a common source of bugs)
#
# ## How the block maps onto AgentCore
#
# | LlamaIndex `BaseMemoryBlock` hook | When the framework calls it      | AgentCore call                                  |
# |-----------------------------------|----------------------------------|-------------------------------------------------|
# | `_aget(messages)`                 | Every turn, before the LLM runs  | `search_long_term_memories(query, namespace)`   |
# | `_aput(messages)`                 | When short-term history flushes  | `create_event(actor_id, session_id, messages)`  |
# | `atruncate(content, n)`           | When assembled memory is too big | (in-memory string trim — no AgentCore call)      |
#
# ## Architecture
#
# ```
#                         LlamaIndex FunctionAgent
#                                   │
#                    agent.run(msg, memory=Memory)
#                                   │
#               ┌───────────────────┴────────────────────┐
#               │            LlamaIndex Memory            │
#               │  short-term FIFO (token-bounded queue)  │
#               │                  │ flush (over budget)  │
#               │                  ▼                      │
#               │     AgentCoreMemoryBlock (BaseMemoryBlock)
#               └──────────┬──────────────────┬───────────┘
#                  _aget    │                  │  _aput
#         search_long_term_memories      create_event
#                          │                  │
#                          ▼                  ▼
#            ┌──────────────────────────────────────────┐
#            │        AgentCore Memory (one memory_id)    │
#            │  Semantic strategy →                       │
#            │    /llamaindex-ltm/{actorId}/facts/        │  ◀── write AND read here
#            └──────────────────────────────────────────┘
# ```
#
# The write path (`create_event` → Semantic extraction) and the read path
# (`search_long_term_memories`) target the **same resolved namespace**. This is deliberate:
# the older memory-as-tool tutorials searched a hard-coded `/strategies/` prefix that did
# **not** match where the Semantic strategy actually wrote, so retrieval silently returned
# nothing. We resolve one namespace template and use it for both directions.
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - AWS IAM permissions:
#   - `bedrock-agentcore:CreateMemory`, `:DeleteMemory`, `:GetMemory`
#   - `bedrock-agentcore:CreateEvent`, `:RetrieveMemoryRecords`
# - Amazon Bedrock model access for Claude Sonnet 4.6 in your region
# - No IAM execution role required — we use a **built-in** Semantic strategy.
# - `pip install -r requirements.txt`

# ## Step 1: Setup and Imports


import asyncio as _asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, List, Optional

from botocore.exceptions import ClientError

# AgentCore Memory: control-plane client (create/delete) + data-plane session manager
# (events + long-term search). Both are verified from the bedrock_agentcore SDK source.
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole as ACMessageRole

# LlamaIndex current memory API: the Memory container + the BaseMemoryBlock base class.
# (ChatMemoryBuffer is deprecated and intentionally NOT used here.)
from llama_index.core.memory import Memory, BaseMemoryBlock, InsertMethod
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.bedrock_converse import BedrockConverse as _BedrockConverseBase
from pydantic import Field, PrivateAttr

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("llamaindex-builtin-memory-block")

# ---- Configuration --------------------------------------------------------------
REGION = os.getenv("AWS_REGION", "us-west-2")

# Claude Sonnet 4.6 on Amazon Bedrock. The `global.` prefix selects the global
# (cross-region) inference endpoint; swap it to pin to a single region.
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

MEMORY_NAME = "LlamaIndexBuiltInMemoryBlock"
ACTOR_ID = "knowledge-user-001"  # WHO the memories belong to

# Namespace TEMPLATE for the Semantic strategy. `{actorId}` is substituted by AgentCore at
# extraction time. We resolve it ONCE (below) and use the resolved value for BOTH writes
# (via the strategy) and reads (via search) so the two never drift apart.
NAMESPACE_TEMPLATE = "/llamaindex-ltm/{actorId}/facts/"
RESOLVED_NAMESPACE = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)

# Long-term extraction is asynchronous — records surface ~30-90s after an event is written.
EXTRACTION_WAIT_SECONDS = 90


# ## Step 2: A Bedrock LLM that is safe to call from async code
#
# `BedrockConverse.achat` uses aiobotocore, which has a credential-loading issue on some
# Python 3.13 setups. We subclass it to run the synchronous `chat` on a worker thread for
# every async entry point the agent uses. This keeps the agent fully async without the
# aiobotocore code path. (Same wrapper used by the sibling LlamaIndex tutorials.)


class BedrockConverse(_BedrockConverseBase):
    """Sync-on-thread wrapper to avoid the aiobotocore Python 3.13 credential issue."""

    async def achat(self, messages, **kwargs):
        return await _asyncio.to_thread(self.chat, messages, **kwargs)

    async def astream_chat(self, messages, **kwargs):
        async def _gen():
            yield await _asyncio.to_thread(self.chat, messages, **kwargs)

        return _gen()

    async def astream_chat_with_tools(
        self,
        tools,
        user_msg=None,
        chat_history=None,
        verbose=False,
        allow_parallel_tool_calls=False,
        tool_required=False,
        **kwargs,
    ):
        chat_kwargs = self._prepare_chat_with_tools_compat(
            tools,
            user_msg=user_msg,
            chat_history=chat_history,
            verbose=verbose,
            allow_parallel_tool_calls=allow_parallel_tool_calls,
            tool_required=tool_required,
            **kwargs,
        )

        async def _gen():
            yield await _asyncio.to_thread(self.chat, **chat_kwargs)

        return _gen()


# ## Step 3: The custom memory block — `BaseMemoryBlock` wrapping AgentCore
#
# This is the heart of the tutorial. `BaseMemoryBlock[str]` is a (pydantic) base whose two
# abstract hooks we implement:
#
#   * `_aget(messages)`  → called EVERY turn. We take the latest user message as the query,
#     run a semantic search over AgentCore long-term memory, and return a formatted string.
#     The `Memory` container injects that string into the agent's context
#     (`InsertMethod.SYSTEM`).
#   * `_aput(messages)`  → called when the short-term FIFO overflows its token budget and
#     ejects old messages. We persist those messages to AgentCore via `create_event`; the
#     Semantic strategy then extracts durable facts from them asynchronously.
#
# IMPORTANT lifecycle detail (verified in `llama_index/core/memory/memory.py`): blocks do
# NOT receive every message — `_aput` only fires for messages **flushed** from short-term
# memory (`from_short_term_memory=True`). For this demo we set a deliberately small
# `token_limit` (Step 5) so flushing — and therefore persistence — happens within a short
# conversation. In production you'd use a realistic limit and persistence happens naturally
# as the conversation grows.


class AgentCoreMemoryBlock(BaseMemoryBlock[str]):
    """A LlamaIndex memory block backed by Amazon Bedrock AgentCore long-term memory.

    Retrieval (`_aget`) runs a semantic search; persistence (`_aput`) writes flushed
    short-term messages as an AgentCore event for the Semantic strategy to extract.
    """

    # --- pydantic fields (configuration) -----------------------------------------
    memory_id: str = Field(description="AgentCore Memory resource id (the shared store).")
    actor_id: str = Field(description="WHO these memories belong to.")
    session_id: str = Field(description="Session the persisted events are grouped under.")
    namespace: str = Field(description="Fully-resolved namespace for BOTH read and write.")
    region: str = Field(default="us-west-2", description="AWS region for AgentCore.")
    retrieval_top_k: int = Field(default=5, description="How many records to retrieve per turn.")

    # The boto-backed session manager is not a pydantic value; keep it as a private,
    # lazily-initialised attribute so the block stays serialisable/clonable.
    _manager: Optional[MemorySessionManager] = PrivateAttr(default=None)

    def _session_manager(self) -> MemorySessionManager:
        """Lazily create (and cache) the AgentCore data-plane session manager."""
        if self._manager is None:
            self._manager = MemorySessionManager(memory_id=self.memory_id, region_name=self.region)
        return self._manager

    @staticmethod
    def _latest_user_text(messages: Optional[List[ChatMessage]]) -> str:
        """Use the most recent user message as the semantic-search query."""
        if not messages:
            return ""
        for message in reversed(messages):
            if message.role == MessageRole.USER and message.content:
                return str(message.content)
        return ""

    async def _aget(self, messages: Optional[List[ChatMessage]] = None, **block_kwargs: Any) -> str:
        """Retrieve relevant long-term records and return them as a context string.

        Called by the framework every turn. Returning "" contributes nothing to context.
        Retrieval failures are swallowed (logged) so a transient memory issue never breaks
        the agent — it simply proceeds without historical context this turn.
        """
        query = self._latest_user_text(messages)
        if not query:
            return ""

        try:
            records = await _asyncio.to_thread(
                self._session_manager().search_long_term_memories,
                query=query,
                namespace_prefix=self.namespace,  # SAME namespace we write to
                top_k=self.retrieval_top_k,
            )
        except ClientError as exc:
            logger.warning("⚠️  Long-term retrieval failed (%s) — proceeding without it.", exc)
            return ""

        texts: List[str] = []
        for record in records:
            # MemoryRecord wraps the service shape {"content": {"text": "..."}, ...}.
            content = record.get("content", {}) if hasattr(record, "get") else {}
            text = content.get("text", "").strip() if isinstance(content, dict) else ""
            if text:
                texts.append(text)

        if not texts:
            return ""

        logger.info("🔎 Retrieved %d long-term record(s) for the agent.", len(texts))
        bullets = "\n".join(f"- {t}" for t in texts)
        return f"What you remember about this user from previous sessions:\n{bullets}"

    async def _aput(self, messages: List[ChatMessage]) -> None:
        """Persist short-term messages ejected from the FIFO to AgentCore.

        Only user/assistant messages with text content are written — empty tool-call
        envelopes are skipped (they can't be reconstructed and add no semantic value).
        """
        to_store: List[ConversationalMessage] = []
        for message in messages:
            if message.role == MessageRole.USER and message.content:
                to_store.append(ConversationalMessage(str(message.content), ACMessageRole.USER))
            elif message.role == MessageRole.ASSISTANT and message.content:
                to_store.append(ConversationalMessage(str(message.content), ACMessageRole.ASSISTANT))

        if not to_store:
            return

        try:
            await _asyncio.to_thread(
                self._session_manager().add_turns,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=to_store,
            )
            logger.info("🧠 Persisted %d flushed message(s) to AgentCore (queued for extraction).", len(to_store))
        except ClientError as exc:
            # Never crash the agent because a memory write failed; log and continue.
            logger.error("❌ Failed to persist flushed messages: %s", exc)

    async def atruncate(self, content: str, tokens_to_truncate: int) -> Optional[str]:
        """Trim retrieved context if assembled memory exceeds the token budget.

        We drop whole bullet lines from the end until we're roughly under budget. Returning
        "" would drop the block entirely; returning None (base default) does the same.
        """
        if tokens_to_truncate <= 0 or not content:
            return content
        lines = content.splitlines()
        # ~4 chars/token is a coarse but standard estimate for trimming display text.
        approx_chars_to_cut = tokens_to_truncate * 4
        cut = 0
        while lines and cut < approx_chars_to_cut:
            cut += len(lines[-1]) + 1
            lines.pop()
        return "\n".join(lines)


# ## Step 4: A small domain tool (so the agent is a realistic FunctionAgent)
#
# The memory block handles persistence and recall automatically — the agent does NOT need a
# "remember this" tool. We give it one lightweight domain tool so it behaves like a normal
# task agent while memory works invisibly underneath.


def note_interest(topic: str, detail: str) -> str:
    """Record that the user is interested in a topic, with a short detail."""
    logger.info("📝 Noted interest: %s — %s", topic, detail)
    return f"Noted your interest in {topic}: {detail}"


# ## Step 5: Wiring the block into the LlamaIndex `Memory` class
#
# `Memory.from_defaults` builds the short-term FIFO and attaches our long-term block.
# `priority=0` means the block is NEVER truncated away (it's our durable recall surface).
# We use a small `token_limit` so the FIFO flushes during this short demo, exercising the
# `_aput` persistence path deterministically.


def build_memory(memory_id: str, session_id: str) -> Memory:
    """Construct a LlamaIndex Memory backed by our AgentCore block for a given session."""
    agentcore_block = AgentCoreMemoryBlock(
        name="agentcore_longterm",  # required by BaseMemoryBlock
        memory_id=memory_id,
        actor_id=ACTOR_ID,
        session_id=session_id,
        namespace=RESOLVED_NAMESPACE,
        region=REGION,
        retrieval_top_k=5,
        priority=0,  # 0 = never truncate this block out of context
    )
    return Memory.from_defaults(
        session_id=session_id,
        token_limit=800,  # SMALL on purpose so the FIFO flushes within the demo
        chat_history_token_ratio=0.5,  # flush when short-term exceeds ~400 tokens
        token_flush_size=120,  # flush ~120 tokens at a time to the block
        memory_blocks=[agentcore_block],
        insert_method=InsertMethod.SYSTEM,  # inject recalled memory into the system message
    )


def build_agent(llm: BedrockConverse) -> FunctionAgent:
    """Create the FunctionAgent. Memory is passed per-run, not bound to the agent."""
    return FunctionAgent(
        tools=[FunctionTool.from_defaults(fn=note_interest)],
        llm=llm,
        system_prompt=(
            "You are a Personal Knowledge Assistant. You build up durable knowledge about "
            "the user over time. When the system context includes 'What you remember about "
            "this user', treat it as established fact and use it to personalise your answer. "
            "Be concise and concrete."
        ),
    )


# ## Step 6: Create the shared memory resource (one built-in Semantic strategy)


def get_or_create_memory(memory_client: MemoryClient) -> str:
    """Create the memory with a built-in Semantic strategy, or reuse it if it exists."""
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "PersonalFacts",
                "description": "Durable facts about the user, extracted from conversation.",
                # The strategy writes records under this template. We retrieve from the
                # SAME template (resolved) — no namespace drift.
                "namespaces": [NAMESPACE_TEMPLATE],
            }
        }
    ]
    # create_or_get_memory creates the resource, or returns the existing one on a name
    # clash — no manual already-exists scan needed. Built-in strategies need NO
    # memory_execution_role_arn.
    memory = memory_client.create_or_get_memory(
        name=MEMORY_NAME,
        strategies=strategies,
        description="LlamaIndex built-in memory block tutorial — semantic LTM",
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    logger.info("✅ Memory with built-in Semantic strategy ready: %s", memory_id)
    return memory_id


# ## Step 7: Drive the demo — build knowledge in one session, recall it in another
#
# Session 1 teaches the assistant facts about the user; the small token budget flushes those
# turns into AgentCore, where the Semantic strategy extracts them. After the extraction wait,
# Session 2 (a brand-new Memory, so an empty short-term FIFO) asks the assistant to recall —
# the only way it can answer is via the block's `_aget` reading AgentCore long-term memory.


async def main() -> None:
    memory_client = MemoryClient(region_name=REGION)
    memory_id: Optional[str] = None
    llm = BedrockConverse(model=MODEL_ID, region_name=REGION)
    agent = build_agent(llm)

    try:
        memory_id = get_or_create_memory(memory_client)
        # Brief data-plane propagation pause after the resource goes ACTIVE.
        time.sleep(10)

        # ---- Session 1: build up knowledge -------------------------------------
        print("\n=== Session 1: building long-term knowledge ===")
        session1 = build_memory(memory_id, session_id=f"session-1-{datetime.now().strftime('%Y%m%d%H%M%S')}")

        session1_turns = [
            "Hi! I'm Dana, a backend engineer. I mostly work in Rust and I'm allergic to peanuts.",
            "I'm planning a team offsite in Lisbon next quarter and I prefer vegetarian restaurants.",
            "For my reading list, note my interest in distributed systems, especially consensus protocols.",
            "I also play classical guitar on weekends and I'm learning Portuguese for the trip.",
        ]
        for turn in session1_turns:
            response = await agent.run(turn, memory=session1)
            print(f"\n👤 {turn}\n🤖 {response}")

        # Flushing is driven by the PUT path: each agent.run() puts messages, and when the
        # short-term FIFO exceeds chat_history_token_ratio * token_limit, the OLDEST messages
        # are ejected to the block's _aput (verified in llama_index/core/memory/memory.py —
        # aget() is read-only and does NOT flush). With our small token_limit the early
        # fact-bearing turns flush during the conversation above. To make sure the LAST
        # fact-bearing turns are pushed out too, we send a couple of short "wind-down" turns
        # that grow the queue past the flush threshold before we wait for extraction.
        for closing in ("Thanks, that's really helpful!", "That's everything for today."):
            await agent.run(closing, memory=session1)

        # ---- Wait for asynchronous semantic extraction -------------------------
        print(f"\n⏳ Waiting ~{EXTRACTION_WAIT_SECONDS}s for AgentCore to extract semantic records...")
        await _asyncio.sleep(EXTRACTION_WAIT_SECONDS)
        print("✅ Extraction window elapsed — long-term records should be searchable.")

        # ---- Session 2: a fresh Memory; recall depends entirely on AgentCore ---
        print("\n=== Session 2: fresh session, recall from long-term memory ===")
        session2 = build_memory(memory_id, session_id=f"session-2-{datetime.now().strftime('%Y%m%d%H%M%S')}")

        recall_prompts = [
            "What do you remember about my programming language preferences and any allergies?",
            "I'm booking dinner for my upcoming offsite — what should you keep in mind about location and food?",
            "Suggest a weekend activity that fits my hobbies.",
        ]
        for prompt in recall_prompts:
            response = await agent.run(prompt, memory=session2)
            print(f"\n👤 {prompt}\n🤖 {response}")

        print(
            "\n✅ Expected: the assistant recalls Rust + peanut allergy, Lisbon + vegetarian, "
            "and classical guitar — none of which were said in Session 2."
        )

    finally:
        # ## Cleanup
        #
        # AgentCore Memory resources are billable. We delete the resource even if a step
        # raised. Comment out this block to keep the memory between runs (get_or_create
        # will reuse it).
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info("✅ Deleted memory: %s", memory_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the real error
                logger.error("Failed to delete memory %s: %s", memory_id, exc)


if __name__ == "__main__":
    _asyncio.run(main())
