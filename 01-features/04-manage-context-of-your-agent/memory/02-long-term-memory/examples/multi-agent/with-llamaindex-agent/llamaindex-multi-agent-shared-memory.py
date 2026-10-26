#!/usr/bin/env python

# # LlamaIndex with AgentCore Memory (Multi-Agent — Shared Memory)
#
# ## Introduction
#
# This tutorial demonstrates a **multi-agent system** built with **LlamaIndex**
# (`FunctionAgent`) where several specialized agents collaborate through a **single shared
# AgentCore Memory resource**. The single-agent tutorials
# (`../../single-agent/with-llamaindex-agent/`) wired memory into one agent via a
# `BaseMemoryBlock`. Here the same memory primitive becomes the **communication substrate**
# for a team: one agent writes, the next reads what it wrote, and so on down a pipeline.
#
# The use case is a **research team**:
#   1. **Research Agent** gathers raw findings on a topic and writes them to memory.
#   2. **Analyst Agent** retrieves the researcher's findings, synthesizes them into
#      insights, and writes the synthesis back to memory.
#   3. **Report Agent** retrieves BOTH the findings and the synthesis, then writes a final
#      executive summary.
#
# Each agent is a **separate LlamaIndex `FunctionAgent`** with its own system prompt and its
# own **`actor_id`**. What ties them together is that they all read/write the **same
# `memory_id`** — a memory resource is just a resource ID, and any number of clients can use
# it concurrently. That is the whole multi-agent pattern: shared `memory_id`, per-agent
# `actor_id`, and a namespace design that decides what is *shared* across the team versus
# *private* to one agent.
#
# > **LlamaIndex API note:** The agents have no tools — they reason over text — so the
# > multi-agent mechanics live entirely in how we route their I/O through the shared
# > AgentCore `MemoryClient` (`create_event` / `retrieve_memories`). This keeps the
# > shared-memory pattern explicit and framework-neutral, mirroring the LangGraph and
# > Claude SDK multi-agent tutorials. (Note: a single agent would instead use the
# > `Memory` + `BaseMemoryBlock` API shown in the single-agent tutorials —
# > `ChatMemoryBuffer` is deprecated.)
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term, Multi-agent                                                           |
# | Agent usecase       | Research Team (Researcher → Analyst → Report Writer)                              |
# | Agentic Framework   | LlamaIndex (`FunctionAgent`)                                                     |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Strategies          | Semantic (facts) — **built-in** (no IAM execution role)                          |
# | Memory components    | One shared memory_id, per-agent actor_id, shared vs private namespaces, create_event, retrieve_memories |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Share ONE AgentCore Memory resource across multiple independent LlamaIndex agents
# - Give each agent its own `actor_id` while all agents target the same `memory_id`
# - Design namespaces so some knowledge is **team-shared** and some is **agent-private**
# - Hand off work between agents: Agent B retrieves what Agent A stored (producer/consumer)
# - Orchestrate a sequential pipeline where each agent builds on the previous one's output
#
# ## How memory is shared (the core idea)
#
# A memory resource is identified by a plain `memory_id`. Three identifiers decide
# who-can-see-what:
#
# | Identifier    | Role in a multi-agent system                                                  |
# |---------------|-------------------------------------------------------------------------------|
# | `memory_id`   | The shared resource. SAME for every agent — this is what makes memory shared. |
# | `actor_id`    | WHO is writing/reading. Each agent has a distinct one, PLUS a team identity.  |
# | `namespace`   | WHERE records land. `{actorId}` is substituted from the event's `actor_id`.   |
#
# The namespace template `"/research-team/{actorId}/knowledge/"` has its `{actorId}`
# placeholder filled from the `actor_id` passed to `create_event`. So the SAME template
# produces a SHARED pool or a PRIVATE slice depending only on which actor_id you write under:
#
#   • Write with actor_id="team-shared"     → "/research-team/team-shared/knowledge/"
#       → the TEAM BLACKBOARD every agent reads from and writes to.
#   • Write with actor_id="research-agent"  → "/research-team/research-agent/knowledge/"
#       → that agent's PRIVATE scratchpad, isolated from the others.
#
# Agent B "sees" Agent A's work because B retrieves from the shared blackboard that A wrote
# to — not because they share a conversation (they don't). This is the producer/consumer
# pattern, with AgentCore Memory as the channel.
#
# ## Architecture
#
# ```
#            ┌───────────────────────────────────────────────────────────────┐
#            │           ONE shared AgentCore Memory  (single memory_id)       │
#            │                                                                 │
#            │   SHARED namespace  (actor_id = "team-shared")                  │
#            │     /research-team/team-shared/knowledge/   ◀── team blackboard │
#            │                                                                 │
#            │   PRIVATE namespaces (actor_id = each agent)                    │
#            │     /research-team/research-agent/knowledge/                    │
#            │     /research-team/analyst-agent/knowledge/                     │
#            └───────────────────────────────────────────────────────────────┘
#                 ▲ write          ▲ read+write          ▲ read
#                 │ shared         │ shared              │ shared
#     ┌───────────┴──────┐ ┌───────┴──────────┐ ┌────────┴─────────┐
#     │  Research Agent  │ │  Analyst Agent   │ │  Report Agent    │
#     │  actor: research │ │  actor: analyst  │ │  actor: report   │
#     │  FunctionAgent   │ │  FunctionAgent   │ │  FunctionAgent   │
#     │  (LlamaIndex)    │ │  (LlamaIndex)    │ │  (LlamaIndex)    │
#     └──────────────────┘ └──────────────────┘ └──────────────────┘
#          1. gather            2. synthesize         3. summarize
#          findings ──▶ memory  read findings ──▶     read findings +
#                               write synthesis       synthesis ──▶ report
#
#   Sequential handoff:  Research ─(wait for extraction)─▶ Analyst ─(wait)─▶ Report
# ```
#
# ## Prerequisites
#
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions AND Amazon Bedrock model access
# - Access to the Claude Sonnet 4.6 model in Amazon Bedrock (request it in the Bedrock
#   console under *Model access*, in your chosen region)
# - No IAM execution role required — we use a **built-in** Semantic strategy.
# - `pip install -r requirements.txt`

# ## Step 1: Setup and Imports


import asyncio as _asyncio
import logging
import os
import time
from datetime import datetime

# AgentCore Memory client (shared by all agents) + the StrategyType enum.
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# LlamaIndex agent + Bedrock LLM. FunctionAgent is the current agent class; the agents here
# carry no tools, so they reason purely over the prompt + retrieved context.
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.llms.bedrock_converse import BedrockConverse as _BedrockConverseBase

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("llamaindex-multi-agent")

# ---- Configuration --------------------------------------------------------------
REGION = os.getenv("AWS_REGION", "us-west-2")

# Claude Sonnet 4.6 on Amazon Bedrock. `global.` selects the cross-region inference
# endpoint; swap the prefix to pin to one region.
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# ---- Actor identities ------------------------------------------------------------
# Every agent shares the SAME memory_id (created in Step 3). They differ by actor_id. Each
# agent has its own actor_id for PRIVATE scratch space, plus one "team" actor_id whose
# namespace is the SHARED blackboard all agents read/write.
RESEARCH_ACTOR_ID = "research-agent"
ANALYST_ACTOR_ID = "analyst-agent"
REPORT_ACTOR_ID = "report-agent"
SHARED_ACTOR_ID = "team-shared"  # records written under this actor form the team blackboard

# One session ties this pipeline run together. All three agents use the same session_id so
# the run reads as one coherent collaboration in the event history.
SESSION_ID = f"research-run-{datetime.now().strftime('%Y%m%d%H%M%S')}"

# Namespace template. `{actorId}` is substituted at extraction time from the actor_id on the
# event. The SAME template yields the shared blackboard or a private slice depending only on
# which actor_id we write under. Always end namespace templates with "/".
NAMESPACE_TEMPLATE = "/research-team/{actorId}/knowledge/"

# The shared blackboard namespace, fully resolved. This is the channel between agents. We
# read from the SAME resolved namespace the previous agent wrote to — no namespace drift.
SHARED_NAMESPACE = NAMESPACE_TEMPLATE.format(actorId=SHARED_ACTOR_ID)

# Long-term extraction is asynchronous — records usually appear ~30-90s after create_event,
# but the tail runs longer (observed past 2 min). Because this is a SEQUENTIAL handoff (each
# agent consumes the previous agent's output), we poll for extraction between stages and cap
# the wait generously so a slow extraction doesn't leave the next agent with an empty board.
EXTRACTION_MAX_WAIT_SECONDS = 300
EXTRACTION_POLL_INTERVAL_SECONDS = 15

# The research topic the whole team works on.
RESEARCH_TOPIC = (
    "The operational trade-offs of adopting vector databases for "
    "retrieval-augmented generation (RAG) in production systems."
)

# Per-agent system prompts. Each agent is a distinct persona with a distinct job in the
# pipeline. They never share a conversation — only the memory resource.
RESEARCH_AGENT_PROMPT = (
    "You are a Research Agent on a research team. Your job is to gather concrete, factual "
    "findings on the assigned topic: specific capabilities, constraints, costs, and "
    "trade-offs. Produce a tight set of discrete findings (bullet points), each a "
    "self-contained, verifiable statement. Do not editorialize or conclude — just surface "
    "the raw findings for your teammates to build on. "
    f"Today's date: {datetime.today().strftime('%Y-%m-%d')}."
)

ANALYST_AGENT_PROMPT = (
    "You are an Analyst Agent on a research team. Your teammate the Research Agent has "
    "gathered raw findings, which are provided to you from shared memory. Your job is to "
    "SYNTHESIZE them: identify themes, tensions, and the decision factors that matter most. "
    "Produce a short synthesis (3-5 insights), each grounded in the findings you were given. "
    "Do not invent facts beyond the provided findings. "
    f"Today's date: {datetime.today().strftime('%Y-%m-%d')}."
)

REPORT_AGENT_PROMPT = (
    "You are a Report Agent on a research team. Both the raw findings (from the Research "
    "Agent) and the synthesis (from the Analyst Agent) are provided to you from shared "
    "memory. Your job is to write a crisp EXECUTIVE SUMMARY for a busy decision-maker: a "
    "one-paragraph overview followed by a short, prioritized recommendation list. Ground "
    "every claim in the provided material. "
    f"Today's date: {datetime.today().strftime('%Y-%m-%d')}."
)


# ## Step 2: A Bedrock LLM that is safe to call from async code
#
# `BedrockConverse.achat` uses aiobotocore, which has a credential-loading issue on some
# Python 3.13 setups. We subclass it to run the synchronous `chat` on a worker thread for
# every async entry point the agent uses — the same wrapper used by the single-agent
# LlamaIndex tutorials.


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


# ## Step 3: Initialize the shared clients
#
# A single Bedrock-backed LLM and a single MemoryClient are reused by all three agents —
# what differs per agent is the actor_id, system prompt, and `FunctionAgent` instance, not
# the underlying clients.


llm = BedrockConverse(model=MODEL_ID, region_name=REGION)
memory_client = MemoryClient(region_name=REGION)
logger.info("✅ Clients initialized for region: %s", REGION)


# ## Step 4: Create the shared memory resource (one built-in Semantic strategy)
#
# This is the resource ALL agents share. We attach a single built-in **Semantic** strategy
# whose namespace template is `/research-team/{actorId}/knowledge/`. Because `{actorId}` is
# substituted from the event's actor_id, this ONE strategy serves both the shared team
# blackboard (actor_id="team-shared") and each agent's private slice — no separate strategy
# per agent. Built-in strategies require NO IAM execution role.


def get_or_create_memory(name: str) -> str:
    """Create the shared-memory resource with a built-in Semantic strategy, or reuse it."""
    strategies = [
        {
            StrategyType.SEMANTIC.value: {
                "name": "ResearchTeamKnowledge",
                "description": "Findings, syntheses, and reports shared across the research team",
                "namespaces": [NAMESPACE_TEMPLATE],
            }
        }
    ]
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # strategies => long-term extraction is enabled
        description="Shared LTM for the LlamaIndex multi-agent research team tutorial",
        event_expiry_days=7,  # retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info("✅ Shared memory with built-in Semantic strategy ready: %s", memory_id)
    return memory_id


# ## Step 5: Memory read/write helpers (the shared channel)
#
# These two helpers are how agents talk to each other. `store_to_memory` writes an agent's
# output under a given actor_id (which selects shared vs private), and `retrieve_from_memory`
# reads back from a resolved namespace. They are deliberately thin wrappers over
# `create_event` / `retrieve_memories` so the multi-agent mechanics stay visible.


def store_to_memory(memory_id: str, actor_id: str, text: str, label: str) -> None:
    """Write an agent's contribution to memory under `actor_id`.

    The actor_id decides WHERE this lands: SHARED_ACTOR_ID writes to the team blackboard; an
    agent's own actor_id writes to its private slice. Because the memory has a Semantic
    strategy, this event is asynchronously extracted into long-term records teammates can
    later retrieve. We write the agent's output as a single USER message.
    """
    try:
        memory_client.create_event(
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=SESSION_ID,
            messages=[(text, "USER")],
        )
        logger.info("🧠 [%s] stored to memory under actor '%s' (queued for extraction)", label, actor_id)
    except Exception as e:  # noqa: BLE001 - a single write failure must not kill the pipeline
        logger.error("Memory save error for %s: %s", label, e)


def retrieve_from_memory(memory_id: str, namespace: str, query: str, top_k: int = 10) -> list:
    """Retrieve extracted records from one fully-resolved namespace.

    `retrieve_memories` runs a semantic search over the namespace and returns the most
    relevant records. The namespace must be FULLY RESOLVED — wildcards are not supported —
    so callers substitute `{actorId}` before calling. Each record is a dict whose
    `content.text` holds the extracted memory.
    """
    try:
        records = memory_client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=top_k,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to retrieve from %s: %s", namespace, e)
        return []

    texts = []
    for record in records:
        # Record shape: {"content": {"text": "..."}, "score": 0.87, ...}
        content = record.get("content", {})
        text = content.get("text", "").strip() if isinstance(content, dict) else ""
        if text:
            texts.append(text)
    logger.info("🔎 Retrieved %d record(s) from %s", len(texts), namespace)
    return texts


def wait_for_records(memory_id: str, namespace: str, query: str) -> None:
    """Poll a namespace until records surface, or until the max wait elapses.

    This is the SEQUENTIAL handoff barrier: extraction is asynchronous, so before the next
    agent retrieves the previous agent's output we wait for that output to be extracted into
    the shared namespace. In production you would not block a pipeline like this between
    every stage. We block here only so the demo's handoff is deterministic.
    """
    logger.info("⏳ Waiting for extraction into %s ...", namespace)
    deadline = time.time() + EXTRACTION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        if retrieve_from_memory(memory_id, namespace, query, top_k=1):
            logger.info("✅ Records are available — handing off to the next agent")
            return
        time.sleep(EXTRACTION_POLL_INTERVAL_SECONDS)
    logger.warning(
        "⚠️ Records did not surface within the wait window. Extraction may still complete "
        "shortly; the next agent will simply see fewer (or no) records from this stage."
    )


# ## Step 6: Run one agent (a self-contained LlamaIndex FunctionAgent)
#
# Each agent is its OWN LlamaIndex agent: its own system prompt, its own actor_id, a fresh
# invocation. The only thing carried between agents is what passes THROUGH memory. `context`
# is the text we retrieved from memory for this agent (empty for the first agent); we fold
# it into the user turn so the agent works from its teammates' output.
#
# We deliberately do NOT pass a `memory=` object to `agent.run`: cross-agent state lives in
# the SHARED AgentCore resource, not in any one agent's LlamaIndex Memory. Each agent runs
# statelessly and the orchestrator moves knowledge between them through memory (Step 7).


async def run_agent(system_prompt: str, task: str, context: str = "") -> str:
    """Build a LlamaIndex FunctionAgent for this persona, run one turn, and return its reply.

    Args:
        system_prompt: The agent's persona/role.
        task: The instruction for this agent.
        context: Knowledge retrieved from shared memory (the previous agents' output).
                 Empty for the first agent in the pipeline.
    """
    user_content = task
    if context:
        user_content = (
            f"{task}\n\n"
            f"--- Knowledge from your teammates (retrieved from shared memory) ---\n"
            f"{context}\n"
            f"--- End of shared knowledge ---"
        )

    # Each agent is a standalone FunctionAgent with no tools — it reasons over the prompt +
    # context. The multi-agent coordination happens OUTSIDE the agent, in how we route its
    # output and input through the shared memory (Step 7).
    agent = FunctionAgent(tools=[], llm=llm, system_prompt=system_prompt)
    response = await agent.run(user_content)
    return str(response)


# ## Step 7: Orchestrate the pipeline
#
# Sequential producer/consumer handoff over the shared memory:
#
#   1. Research Agent gathers findings → writes them to the SHARED blackboard.
#      (wait for extraction)
#   2. Analyst Agent retrieves the findings from the shared blackboard → synthesizes →
#      writes the synthesis back to the SHARED blackboard.
#      (wait for extraction)
#   3. Report Agent retrieves BOTH findings and synthesis from the shared blackboard →
#      writes the final executive summary.
#
# Every retrieve hits the SAME shared namespace the previous agent wrote to — that is how
# Agent B sees Agent A's work despite never sharing a conversation.


async def main() -> None:
    memory_id = None  # init so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("LlamaIndexMultiAgentSharedMemory")
        # Brief data-plane propagation pause after the resource goes ACTIVE.
        time.sleep(10)

        # ---- Stage 1: Research Agent --------------------------------------------
        # First agent in the pipeline: no upstream context. It gathers findings and
        # publishes them to the SHARED blackboard so the rest of the team can read them. It
        # also keeps a copy in its PRIVATE slice to show agent-specific storage.
        print("\n=== Stage 1: Research Agent (gather findings) ===")
        findings = await run_agent(
            system_prompt=RESEARCH_AGENT_PROMPT,
            task=f"Gather concrete findings on this topic:\n{RESEARCH_TOPIC}",
        )
        print(f"Research Agent:\n{findings}\n")
        store_to_memory(memory_id, SHARED_ACTOR_ID, findings, label="research findings → shared")
        store_to_memory(memory_id, RESEARCH_ACTOR_ID, findings, label="research findings → private")

        # Wait for the researcher's findings to be extracted before the analyst reads them.
        wait_for_records(memory_id, SHARED_NAMESPACE, query="research findings on the topic")

        # ---- Stage 2: Analyst Agent ---------------------------------------------
        # Consumes the researcher's findings FROM SHARED MEMORY (it was never in the
        # researcher's conversation), synthesizes, and publishes the synthesis back.
        print("=== Stage 2: Analyst Agent (synthesize findings) ===")
        prior_findings = retrieve_from_memory(
            memory_id, SHARED_NAMESPACE, query="key findings and trade-offs about vector databases for RAG"
        )
        context_for_analyst = "\n".join(f"- {f}" for f in prior_findings)
        synthesis = await run_agent(
            system_prompt=ANALYST_AGENT_PROMPT,
            task="Synthesize the research findings into the insights that matter most.",
            context=context_for_analyst,
        )
        print(f"Analyst Agent:\n{synthesis}\n")
        store_to_memory(memory_id, SHARED_ACTOR_ID, synthesis, label="analysis synthesis → shared")
        store_to_memory(memory_id, ANALYST_ACTOR_ID, synthesis, label="analysis synthesis → private")

        # Wait for the synthesis to be extracted before the report agent reads it.
        wait_for_records(memory_id, SHARED_NAMESPACE, query="synthesis and key insights")

        # ---- Stage 3: Report Agent ----------------------------------------------
        # Consumes BOTH the findings and the synthesis from shared memory and writes the
        # final executive summary. A single retrieve over the shared blackboard returns
        # everything the team has published so far.
        print("=== Stage 3: Report Agent (write executive summary) ===")
        all_team_knowledge = retrieve_from_memory(
            memory_id,
            SHARED_NAMESPACE,
            query="research findings and analytical synthesis about vector databases for RAG",
        )
        context_for_report = "\n".join(f"- {k}" for k in all_team_knowledge)
        report = await run_agent(
            system_prompt=REPORT_AGENT_PROMPT,
            task="Write the executive summary and prioritized recommendations.",
            context=context_for_report,
        )
        print(f"Report Agent:\n{report}\n")
        store_to_memory(memory_id, SHARED_ACTOR_ID, report, label="final report → shared")
        store_to_memory(memory_id, REPORT_ACTOR_ID, report, label="final report → private")

        # ---- Inspect what's in shared vs agent-specific memory ------------------
        # The shared blackboard now holds the whole team's output; each private slice holds
        # only that one agent's contribution. This is the shared-vs-isolated view.
        print("=== What lives in SHARED memory (the team blackboard) ===")
        for item in retrieve_from_memory(memory_id, SHARED_NAMESPACE, query="everything the team produced"):
            print(f"  • {item}")
        print()

        print("=== What lives in the Research Agent's PRIVATE memory ===")
        research_private_ns = NAMESPACE_TEMPLATE.format(actorId=RESEARCH_ACTOR_ID)
        for item in retrieve_from_memory(memory_id, research_private_ns, query="this agent's own findings"):
            print(f"  • {item}")
        print()

    finally:
        # ## Cleanup
        #
        # AgentCore Memory resources are billable, so we delete the resource when the demo
        # finishes. To keep the memory between runs (e.g. to inspect it in the console),
        # comment out the block below — `get_or_create_memory` will reuse it.
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info("✅ Deleted memory: %s", memory_id)
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to delete memory %s: %s", memory_id, e)


if __name__ == "__main__":
    _asyncio.run(main())
