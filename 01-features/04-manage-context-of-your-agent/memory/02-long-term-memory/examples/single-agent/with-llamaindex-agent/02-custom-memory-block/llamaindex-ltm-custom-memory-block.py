#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory - Custom Memory Block (Self-managed extraction)
#
# ## Introduction
#
# This tutorial builds on the built-in memory block (`../01-built-in-memory-block/`) with a
# **more sophisticated** custom `BaseMemoryBlock` that owns its extraction logic — the
# "self-managed" philosophy applied at the **block level**. Where the built-in block wrote
# raw flushed turns and let AgentCore's Semantic strategy decide what to keep, this block:
#
#   1. **Decides what to store (conditional logic).** Before writing anything, it asks the
#      LLM to distill the flushed turns into a single durable fact AND to judge whether the
#      content is memory-worthy. Greetings, chit-chat, and transient questions are dropped;
#      only stable, reusable facts are persisted.
#   2. **Distills before writing (self-managed extraction).** It writes the *distilled* fact
#      — not the raw conversation — so the stored record is clean and high-signal. You own
#      the "what to extract" step instead of delegating it entirely to the service.
#   3. **Filters retrieval by score.** On read it runs a semantic search and keeps only
#      records whose relevance `score` clears a threshold, so weak matches never pollute the
#      agent's context.
#
# > ### ⚠️ LlamaIndex API note — `Memory` + `BaseMemoryBlock` (NOT `ChatMemoryBuffer`)
# > `ChatMemoryBuffer` is **deprecated**. This block subclasses `BaseMemoryBlock[str]` and
# > implements `_aget` (retrieve, every turn) and `_aput` (persist, on short-term flush),
# > exactly as the built-in tutorial. Verified against `llama-index-core` 0.14.x.
#
# > ### 🛠️ "Self-managed" — two levels, and where this sits
# > AgentCore has a *server-side* self-managed strategy (`customMemoryStrategy` +
# > `selfManagedConfiguration`) where AgentCore drops conversation payloads to **your S3
# > bucket**, notifies **your SNS topic**, and **your** Lambda writes records back via
# > `BatchCreateMemoryRecords`. That requires standing infrastructure and is documented at
# > [`../../../../03-self-managed-strategy/`](../../../../03-self-managed-strategy/).
# >
# > THIS tutorial shows the **client-side / in-process** form of self-managed extraction:
# > the LlamaIndex block runs the extraction itself, synchronously, with no extra infra, and
# > stores the distilled result. It's the right pattern when your extraction logic is light
# > enough to run in the agent process and you want zero operational overhead.
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term, single-agent                                                          |
# | Agent usecase       | Customer Support Assistant (durable customer profile across contacts)            |
# | Agentic Framework   | LlamaIndex (`Memory` + custom `BaseMemoryBlock`, `FunctionAgent`)                |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Strategies          | Semantic (facts) — **built-in** strategy; extraction *gated* by the block        |
# | Memory components    | Conditional `_aput` (LLM-judged), score-filtered `_aget`, client-side distillation |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Add conditional storage to a memory block (don't persist low-value turns)
# - Run a client-side extraction/distillation step before writing (self-managed)
# - Score-filter retrieved records so only strong matches reach the LLM
# - Keep the write namespace and read namespace identical (avoid the classic drift bug)
#
# ## Architecture
#
# ```
#                          LlamaIndex FunctionAgent
#                                    │
#                     agent.run(msg, memory=Memory)
#                                    │
#               ┌────────────────────┴─────────────────────┐
#               │             LlamaIndex Memory             │
#               │   short-term FIFO (token-bounded queue)   │
#               │                   │ flush (over budget)   │
#               │                   ▼                       │
#               │   SelfManagedMemoryBlock (BaseMemoryBlock)│
#               │     _aput:  LLM distill ─▶ worth storing? ─┼─ no ─▶ drop
#               │                              │ yes          │
#               │     _aget:  search ─▶ score ≥ threshold?   │
#               └───────────┬───────────────────┬───────────┘
#                  search    │                   │  create_event
#            (filter by score)                  (distilled fact only)
#                           ▼                    ▼
#             ┌─────────────────────────────────────────────┐
#             │         AgentCore Memory (one memory_id)      │
#             │   Semantic strategy →                         │
#             │     /support/{actorId}/profile/               │  ◀── write AND read here
#             └─────────────────────────────────────────────┘
# ```
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - IAM permissions: `bedrock-agentcore:CreateMemory`, `:DeleteMemory`, `:GetMemory`,
#   `:CreateEvent`, `:RetrieveMemoryRecords`
# - Amazon Bedrock model access for Claude Sonnet 4.6 in your region
# - No IAM execution role required — we use a **built-in** Semantic strategy; the extra
#   logic (gating + distillation) runs in-process.
# - `pip install -r requirements.txt`

# ## Step 1: Setup and Imports


import asyncio as _asyncio
import json
import logging
import os
import time
from datetime import datetime
from typing import Any, List, Optional

from botocore.exceptions import ClientError

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from bedrock_agentcore.memory.session import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole as ACMessageRole

from llama_index.core.memory import Memory, BaseMemoryBlock, InsertMethod
from llama_index.core.base.llms.types import ChatMessage, MessageRole
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.bedrock_converse import BedrockConverse as _BedrockConverseBase
from pydantic import Field, PrivateAttr

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("llamaindex-custom-memory-block")

# ---- Configuration --------------------------------------------------------------
REGION = os.getenv("AWS_REGION", "us-west-2")
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

MEMORY_NAME = "LlamaIndexCustomMemoryBlock"
ACTOR_ID = "customer-7741"  # the customer whose durable profile we build

# Namespace template for the Semantic strategy. Resolved ONCE and used for BOTH the
# strategy's write target and the block's read query — no drift between write and read.
NAMESPACE_TEMPLATE = "/support/{actorId}/profile/"
RESOLVED_NAMESPACE = NAMESPACE_TEMPLATE.format(actorId=ACTOR_ID)

EXTRACTION_WAIT_SECONDS = 90

# Retrieval: only records at or above this relevance score reach the agent. AgentCore
# returns a `score` (0-1, higher = more relevant) on each record from RetrieveMemoryRecords.
MIN_RELEVANCE_SCORE = 0.3


# ## Step 2: A Bedrock LLM safe to call from async code
#
# Same sync-on-thread wrapper as the sibling tutorials: avoids the aiobotocore Python 3.13
# credential-loading issue by running synchronous `chat` on a worker thread.


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


# ## Step 3: The self-managed custom memory block
#
# The block holds its OWN reference to an LLM (`_extractor_llm`) used purely for the
# extraction/gating step — separate from the agent's conversation LLM. On flush it distills,
# decides, and conditionally writes; on retrieval it searches and score-filters.


# Prompt the block uses to distill + gate flushed turns. We ask for strict JSON so parsing
# is deterministic; on any parse failure we fail SAFE (store nothing) rather than guess.
_EXTRACTION_PROMPT = """You maintain a durable customer-support profile. Given the recent \
conversation snippet below, extract ONLY stable, reusable facts about the customer \
(identity, account, preferences, recurring issues, entitlements). Ignore greetings, \
pleasantries, one-off questions, and anything transient.

Respond with STRICT JSON and nothing else:
{{"worth_storing": <true|false>, "fact": "<one concise sentence, or empty string>"}}

Conversation snippet:
{snippet}
"""


class SelfManagedMemoryBlock(BaseMemoryBlock[str]):
    """A custom memory block that owns its extraction: conditional, distilled writes and
    score-filtered reads, backed by Amazon Bedrock AgentCore long-term memory."""

    # --- pydantic fields ---------------------------------------------------------
    memory_id: str = Field(description="AgentCore Memory resource id.")
    actor_id: str = Field(description="WHO the profile belongs to.")
    session_id: str = Field(description="Session events are grouped under.")
    namespace: str = Field(description="Fully-resolved namespace for BOTH read and write.")
    region: str = Field(default="us-west-2", description="AWS region for AgentCore.")
    retrieval_top_k: int = Field(default=8, description="Candidates to fetch before score-filtering.")
    min_score: float = Field(default=0.3, description="Drop retrieved records below this relevance score.")

    # Non-pydantic runtime collaborators (lazy / injected) kept as private attrs.
    _manager: Optional[MemorySessionManager] = PrivateAttr(default=None)
    _extractor_llm: Any = PrivateAttr(default=None)

    def bind_extractor(self, llm: Any) -> "SelfManagedMemoryBlock":
        """Inject the LLM used for the distillation/gating step. Returns self for chaining."""
        self._extractor_llm = llm
        return self

    def _session_manager(self) -> MemorySessionManager:
        if self._manager is None:
            self._manager = MemorySessionManager(memory_id=self.memory_id, region_name=self.region)
        return self._manager

    @staticmethod
    def _latest_user_text(messages: Optional[List[ChatMessage]]) -> str:
        if not messages:
            return ""
        for message in reversed(messages):
            if message.role == MessageRole.USER and message.content:
                return str(message.content)
        return ""

    # ---- RETRIEVAL: search + score filter ---------------------------------------
    async def _aget(self, messages: Optional[List[ChatMessage]] = None, **block_kwargs: Any) -> str:
        """Retrieve relevant profile facts, keeping only those above the score threshold."""
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
            logger.warning("⚠️  Retrieval failed (%s) — proceeding without profile context.", exc)
            return ""

        kept: List[str] = []
        dropped = 0
        for record in records:
            # `score` is an optional Double on each MemoryRecordSummary (verified in the
            # bedrock-agentcore service model). Treat a missing score as 0.0 → conservative.
            score = record.get("score", 0.0) if hasattr(record, "get") else 0.0
            content = record.get("content", {}) if hasattr(record, "get") else {}
            text = content.get("text", "").strip() if isinstance(content, dict) else ""
            if not text:
                continue
            if score is not None and score >= self.min_score:
                kept.append(text)
            else:
                dropped += 1

        logger.info("🔎 Retrieval: kept %d record(s), dropped %d below score %.2f.", len(kept), dropped, self.min_score)
        if not kept:
            return ""
        bullets = "\n".join(f"- {t}" for t in kept)
        return f"Known facts about this customer (high-confidence):\n{bullets}"

    # ---- PERSISTENCE: distill + gate + conditional write ------------------------
    async def _aput(self, messages: List[ChatMessage]) -> None:
        """Distill flushed turns into one durable fact and store it ONLY if worth keeping."""
        snippet = self._format_snippet(messages)
        if not snippet:
            return

        decision = await self._extract_fact(snippet)
        if not decision.get("worth_storing") or not decision.get("fact"):
            logger.info("🚫 Nothing memory-worthy in flushed turns — skipping write.")
            return

        fact = str(decision["fact"]).strip()
        try:
            # Write the DISTILLED fact (not the raw turns). It's stored as a USER message so
            # the Semantic strategy extracts/embeds it; retrieval then finds it by meaning.
            await _asyncio.to_thread(
                self._session_manager().add_turns,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[ConversationalMessage(fact, ACMessageRole.USER)],
            )
            logger.info("🧠 Stored distilled fact: %r", fact)
        except ClientError as exc:
            logger.error("❌ Failed to store distilled fact: %s", exc)

    @staticmethod
    def _format_snippet(messages: List[ChatMessage]) -> str:
        """Render flushed messages into a compact transcript for the extractor."""
        lines: List[str] = []
        for message in messages:
            if message.role in (MessageRole.USER, MessageRole.ASSISTANT) and message.content:
                who = "Customer" if message.role == MessageRole.USER else "Agent"
                lines.append(f"{who}: {message.content}")
        return "\n".join(lines)

    async def _extract_fact(self, snippet: str) -> dict:
        """Run the in-process extractor LLM and parse its strict-JSON verdict.

        Fails SAFE: any error or unparseable output → store nothing. We never fabricate a
        fact or guess intent when the extractor's output is malformed.
        """
        if self._extractor_llm is None:
            logger.warning("No extractor LLM bound — skipping extraction. Call bind_extractor().")
            return {"worth_storing": False, "fact": ""}

        prompt = _EXTRACTION_PROMPT.format(snippet=snippet)
        try:
            response = await self._extractor_llm.achat([ChatMessage(role=MessageRole.USER, content=prompt)])
            raw = str(response.message.content).strip()
            # Be tolerant of fenced code blocks the model might wrap JSON in.
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw[raw.find("{") : raw.rfind("}") + 1]
            parsed = json.loads(raw)
            return {
                "worth_storing": bool(parsed.get("worth_storing", False)),
                "fact": str(parsed.get("fact", "")),
            }
        except (json.JSONDecodeError, AttributeError, KeyError, ValueError) as exc:
            logger.warning("⚠️  Could not parse extractor output (%s) — failing safe (no write).", exc)
            return {"worth_storing": False, "fact": ""}

    async def atruncate(self, content: str, tokens_to_truncate: int) -> Optional[str]:
        """Trim retrieved context from the end if the assembled memory is over budget."""
        if tokens_to_truncate <= 0 or not content:
            return content
        lines = content.splitlines()
        approx_chars_to_cut = tokens_to_truncate * 4  # ~4 chars/token coarse estimate
        cut = 0
        while lines and cut < approx_chars_to_cut:
            cut += len(lines[-1]) + 1
            lines.pop()
        return "\n".join(lines)


# ## Step 4: A domain tool (so the agent is a realistic support FunctionAgent)


def open_ticket(summary: str, priority: str) -> str:
    """Open a support ticket with a short summary and a priority (low|medium|high)."""
    logger.info("🎫 Opened %s-priority ticket: %s", priority, summary)
    return f"Opened a {priority}-priority ticket: {summary}"


# ## Step 5: Wire the block into the LlamaIndex Memory class


def build_memory(memory_id: str, session_id: str, extractor_llm: BedrockConverse) -> Memory:
    """Construct a Memory backed by the self-managed block for a given session."""
    block = SelfManagedMemoryBlock(
        name="self_managed_profile",  # required by BaseMemoryBlock
        memory_id=memory_id,
        actor_id=ACTOR_ID,
        session_id=session_id,
        namespace=RESOLVED_NAMESPACE,
        region=REGION,
        retrieval_top_k=8,
        min_score=MIN_RELEVANCE_SCORE,
        priority=0,  # never truncate the profile block out of context
    ).bind_extractor(extractor_llm)

    return Memory.from_defaults(
        session_id=session_id,
        token_limit=800,  # small on purpose so the FIFO flushes within the demo
        chat_history_token_ratio=0.5,
        token_flush_size=120,
        memory_blocks=[block],
        insert_method=InsertMethod.SYSTEM,
    )


def build_agent(llm: BedrockConverse) -> FunctionAgent:
    return FunctionAgent(
        tools=[FunctionTool.from_defaults(fn=open_ticket)],
        llm=llm,
        system_prompt=(
            "You are a Customer Support Assistant. When the system context lists 'Known facts "
            "about this customer', use them to personalise and speed up support. Be concise, "
            "empathetic, and proactive. Open a ticket when an issue needs follow-up."
        ),
    )


# ## Step 6: Create the shared memory resource (built-in Semantic strategy)


def get_or_create_memory(memory_client: MemoryClient) -> str:
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "CustomerProfile",
                "description": "Durable, distilled facts about the customer.",
                "namespaces": [NAMESPACE_TEMPLATE],  # write target == read target
            }
        }
    ]
    # create_or_get_memory creates the memory on first run and, on a name clash, returns the
    # existing memory dict instead of erroring — so reruns reuse the same resource.
    memory = memory_client.create_or_get_memory(
        name=MEMORY_NAME,
        strategies=strategies,
        description="LlamaIndex custom (self-managed) memory block tutorial",
        event_expiry_days=30,
    )
    memory_id = memory["id"]
    logger.info("✅ Memory with built-in Semantic strategy ready: %s", memory_id)
    return memory_id


# ## Step 7: Drive the demo
#
# Session 1 mixes memory-worthy facts with throwaway chit-chat — the block should store the
# former and drop the latter. After extraction, Session 2 (fresh Memory) verifies recall and
# that only the high-value, score-clearing facts surface.


async def main() -> None:
    memory_client = MemoryClient(region_name=REGION)
    memory_id: Optional[str] = None
    conversation_llm = BedrockConverse(model=MODEL_ID, region_name=REGION)
    extractor_llm = BedrockConverse(model=MODEL_ID, region_name=REGION)
    agent = build_agent(conversation_llm)

    try:
        memory_id = get_or_create_memory(memory_client)
        time.sleep(10)  # brief data-plane propagation after ACTIVE

        # ---- Session 1: mixed signal --------------------------------------------
        print("\n=== Session 1: build profile (block gates what gets stored) ===")
        session1 = build_memory(memory_id, f"s1-{datetime.now().strftime('%Y%m%d%H%M%S')}", extractor_llm)

        session1_turns = [
            "Hey, good morning! Hope you're doing well today.",  # chit-chat → should be dropped
            "I'm Priya Nair, account #AC-7741. I'm on the Enterprise plan with the SSO add-on.",  # store
            "By the way, nice weather we're having.",  # chit-chat → drop
            "Heads up: I always need invoices in EUR and sent to billing@nair-corp.eu.",  # store
            "I keep hitting a rate-limit error on the bulk-export endpoint every Monday morning.",  # store
            "Thanks, that's all for now!",  # chit-chat → drop
        ]
        for turn in session1_turns:
            response = await agent.run(turn, memory=session1)
            print(f"\n👤 {turn}\n🤖 {response}")

        # Flushing happens on the PUT path: as agent.run() puts messages and the short-term
        # FIFO exceeds chat_history_token_ratio * token_limit, the OLDEST messages are
        # ejected to the block's _aput (verified in llama_index/core/memory/memory.py —
        # aget() is read-only and does NOT flush). A couple of short wind-down turns grow the
        # queue past the threshold so the last fact-bearing turns are flushed too. The block
        # then GATES each flushed snippet — these closers should be judged not memory-worthy.
        for closing in ("Thanks, appreciate the help!", "That's all for now."):
            await agent.run(closing, memory=session1)

        print(f"\n⏳ Waiting ~{EXTRACTION_WAIT_SECONDS}s for semantic extraction...")
        await _asyncio.sleep(EXTRACTION_WAIT_SECONDS)
        print("✅ Extraction window elapsed.")

        # ---- Session 2: recall, score-filtered ----------------------------------
        print("\n=== Session 2: fresh session, score-filtered recall ===")
        session2 = build_memory(memory_id, f"s2-{datetime.now().strftime('%Y%m%d%H%M%S')}", extractor_llm)

        recall_prompts = [
            "Remind me which plan and add-ons this account has.",
            "I need a new invoice — what are the billing requirements on file?",
            "I'm seeing that recurring export problem again. Can you open a ticket with the context you have?",
        ]
        for prompt in recall_prompts:
            response = await agent.run(prompt, memory=session2)
            print(f"\n👤 {prompt}\n🤖 {response}")

        print(
            "\n✅ Expected: Enterprise + SSO, EUR invoices to billing@nair-corp.eu, and the "
            "Monday bulk-export rate-limit issue are recalled; the weather/greeting chit-chat is not."
        )

    finally:
        # ## Cleanup — resources are billable; delete even if a step raised.
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info("✅ Deleted memory: %s", memory_id)
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask the real error
                logger.error("Failed to delete memory %s: %s", memory_id, exc)


if __name__ == "__main__":
    _asyncio.run(main())
