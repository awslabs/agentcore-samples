#!/usr/bin/env python

# # Claude SDK with AgentCore Memory (Long-Term Memory — Episodic Strategy)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **debugging assistant** using the
# **Anthropic Claude SDK** (via Amazon Bedrock) with AgentCore **long-term memory**
# powered by the **built-in Episodic strategy**. Where the Semantic strategy distills
# isolated *facts* about the user (see `01-built-in-strategies`), the Episodic strategy
# captures whole *episodes* — meaningful, multi-turn interaction sequences that hang
# together as one event ("debugged a memory leak in the payment service on Tuesday").
#
# That difference is the whole point of this tutorial:
#   - **Semantic** answers "what facts do I know about this user?"
#     → "The user runs a Python payment service."
#   - **Episodic** answers "what happened last time, and how did it go?"
#     → "Last Tuesday we chased a memory leak; the root cause was an unbounded cache
#        with no TTL; fixed in v2.4.1." plus cross-episode **reflections** that surface
#        patterns ("unbounded caches are a recurring leak source for this team").
#
# Because the Anthropic SDK is a **stateless API client** with NO built-in conversation
# management or hooks, the integration is fully explicit. We:
#   1. create a memory resource with the EPISODIC strategy,
#   2. run MULTIPLE distinct debugging sessions with Claude — each session is one
#      **episode**, persisted turn-by-turn with `create_event`,
#   3. wait for the asynchronous Extraction → Consolidation → Reflection pipeline,
#   4. retrieve past episodes with `retrieve_memories`, and
#   5. inject them into the system prompt of a *new* debugging session so the agent
#      recalls "what happened last time" — not just static facts.
#
# **NOTE:** Built-in strategies (Episodic included) do NOT require an IAM execution role.
# AgentCore Memory manages the extraction/consolidation/reflection models for you. To
# customize those models or prompts, see the strategy-override examples under
# `02-long-term-memory` (the Episodic override key is `episodicMemoryStrategy` inside a
# `customMemoryStrategy`, with `episodicExtractionOverride` / `episodicConsolidationOverride`
# / `episodicReflectionOverride` configuration blocks).
#
#
# ### A note on "episodic" examples elsewhere in this repo
#
# Some sibling examples are *named* "episodic" but actually configure a **Semantic**
# strategy (`semanticMemoryStrategy`) or a `customMemoryStrategy` with a semantic
# override — not the dedicated Episodic strategy. This tutorial deliberately uses the
# REAL built-in Episodic strategy, whose wire key is `episodicMemoryStrategy`
# (verified against the SDK: `bedrock_agentcore.memory.constants.StrategyType.EPISODIC`).
# See the README's "Verifying the strategy" section for how to confirm this at runtime.
#
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                         |
# | Agent type          | Debugging Assistant                                                              |
# | Agentic Framework   | Anthropic Claude SDK (no framework)                                              |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Tutorial components | AgentCore Episodic Strategy, create_event (per session), retrieve_memories       |
# | Example complexity  | Intermediate                                                                     |
#
# You'll learn to:
# - Create a memory resource with the built-in **Episodic** strategy (no IAM role required)
# - Call Claude through Amazon Bedrock using the `AnthropicBedrock` client
# - Run MULTIPLE sessions, each a distinct episode, storing turns with `create_event`
# - Wait for the asynchronous Extraction → Consolidation → Reflection pipeline
# - Retrieve past episodes (and cross-episode reflections) with `retrieve_memories`
# - Inject episodic recall into a new session so the agent knows "what happened last time"
#
# ## Architecture
#
# ```
#   ┌──────────────┐    1. create_event (per session)     ┌─────────────────────────────┐
#   │  Your code   │ ───  each multi-turn session  ──────▶ │  AgentCore Memory           │
#   │ (messages[]) │      = ONE episode                    │                             │
#   │              │                                       │  short-term events ──┐      │
#   │  session A ──┤                                       │                      │      │
#   │  session B ──┤                                       │  2. async episodic   ▼      │
#   │  session C ──┤                                       │     pipeline:               │
#   │              │                                       │     Extraction →            │
#   │              │                                       │     Consolidation →         │
#   │              │ ◀── 3. retrieve_memories ──────────── │     Reflection   long-term  │
#   │              │     (episodes namespace)              │   • Episodes      records   │
#   └──────┬───────┘                                       │   • Reflections             │
#          │                                               └─────────────────────────────┘
#          │ 4. inject past episodes into system prompt, then messages.create(...)
#          ▼
#   ┌──────────────┐
#   │ Claude via   │  5. assistant reply, now aware of HOW past sessions went
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
from typing import Optional

# The Anthropic SDK's Amazon Bedrock client. `pip install "anthropic[bedrock]"`
# provides this; it signs requests with your AWS credentials (SigV4) and speaks the
# Messages API against Bedrock — no Anthropic API key required.
from anthropic import AnthropicBedrock

# AgentCore Memory client and the StrategyType enum (gives us the exact strategy keys).
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("claude-sdk-ltm-episodic")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for both Bedrock and Memory
ACTOR_ID = "developer_001"  # Any unique identifier for the end-user / agent

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock.
# The `global.` prefix selects the global (cross-region) inference endpoint, which is
# the default for Sonnet 4.6 and carries no regional pricing premium. To pin traffic
# to a region instead, swap the prefix (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# The Episodic strategy writes EPISODE records to this namespace. We use an ACTOR-level
# template (no {sessionId}) so a single retrieval surfaces episodes from ALL of the
# developer's past sessions — exactly what "what happened in previous debugging sessions?"
# needs. `{actorId}` is substituted at extraction time, keeping each developer's episodes
# isolated. The SDK default for EPISODIC is session-scoped
# (/strategies/{memoryStrategyId}/actors/{actorId}/sessions/{sessionId}/); we override it
# here precisely so episodes accumulate per-actor and remain retrievable across sessions.
# NOTE: we pass this under `namespaceTemplates` (the current field). The older `namespaces`
# field was deprecated 2026-03-02 in favor of `namespaceTemplates`; the service stores the
# same value either way, so the resolved retrieval namespace below is unchanged.
EPISODIC_NAMESPACE = "/episodes/{actorId}/"

# Cross-episode REFLECTIONS (insights folded across episodes, e.g. "unbounded caches are a
# recurring leak source for this team") need their own namespace, and the service REQUIRES
# `reflectionConfiguration` for the episodic strategy — omitting it is the root cause of
# the CreateMemory ValidationException this tutorial previously hit.
#
# The service also enforces a SECOND rule the API model doesn't declare: the reflection
# namespace "must be the same as or a prefix of the episodic namespace." A disjoint path
# (e.g. "/reflections/{actorId}/") is rejected. We set the reflection namespace EQUAL to
# the episode namespace, which satisfies the "same as" rule and keeps this tutorial's
# single-namespace design intact: both episodes and reflections land in
# "/episodes/{actorId}/", so one `retrieve_memories` call surfaces both — exactly the
# "episodes (and cross-episode reflections)" behavior the retrieval code documents.
# (The repo's working episodic example instead NESTS them — reflection
# "/meetings/actor/{actorId}/" as a prefix of episode "/meetings/actor/{actorId}/episodes/";
# either shape is valid. Equal is the smaller change here.)
REFLECTION_NAMESPACE = EPISODIC_NAMESPACE

# Episodic extraction is asynchronous and runs THREE steps (Extraction → Consolidation →
# Reflection), so it takes far longer to surface than a single-step semantic extraction.
# Episodes typically appear ~1 min after a session's events are written, but Reflection
# can take 10–15 minutes (per AWS guidance and the repo's working episodic examples). We
# poll but cap the total wait at 15 minutes so the demo reliably surfaces episode records.
EXTRACTION_MAX_WAIT_SECONDS = 1200  # 20 minutes — episodes observed surfacing ~16 min after seeding
EXTRACTION_POLL_INTERVAL_SECONDS = 15

# Base persona for the assistant. When we resume, we APPEND retrieved episodes to this.
SYSTEM_PROMPT = (
    "You are an expert debugging assistant. You help developers diagnose and fix software "
    "defects methodically: clarify symptoms, form hypotheses, isolate the root cause, and "
    "confirm the fix. Be concise and concrete. "
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


# ## Step 3: Create the Memory Resource (with the built-in EPISODIC strategy)
#
# This is the key difference from the semantic tutorial. There we attached Semantic +
# User Preference strategies, which extract standalone facts. Here we attach the
# **Episodic** strategy, which captures whole interaction sequences as episodes and runs
# an extra **Reflection** step to derive cross-episode insights.
#
# The strategy is a single-key dict whose key is the wire value of
# `StrategyType.EPISODIC` — i.e. the literal string `"episodicMemoryStrategy"`. We use
# the enum (`StrategyType.EPISODIC.value`) rather than hardcoding the string so the code
# stays correct if the SDK ever changes the wire name.
#
# Built-in strategies require NO IAM execution role — AgentCore manages the extraction,
# consolidation, AND reflection models. `create_or_get_memory` blocks until the resource
# is ACTIVE, and on a name clash it returns the existing memory instead of erroring — so
# re-running the tutorial reuses the same memory by name.


def get_or_create_memory(name: str) -> str:
    """Create a memory with the built-in Episodic strategy, or reuse it if it exists."""
    # StrategyType.EPISODIC.value == "episodicMemoryStrategy" (verified in the SDK:
    # bedrock_agentcore/memory/constants.py). This is the REAL episodic strategy — not a
    # semantic strategy renamed "episodic", which is a mistake some examples make.
    strategies = [
        {
            StrategyType.EPISODIC.value: {
                "name": "DebuggingEpisodes",
                "description": (
                    "Captures complete debugging sessions as episodes (situation, "
                    "intent, actions, outcome) and reflects across them to surface "
                    "recurring root causes and effective strategies."
                ),
                # Episode records land here (current field is `namespaceTemplates`; the
                # legacy `namespaces` field was deprecated 2026-03-02).
                "namespaceTemplates": [EPISODIC_NAMESPACE],
                # REQUIRED by the service for the episodic strategy. Omitting this is what
                # caused the CreateMemory ValidationException. The Reflection step writes
                # cross-episode insights into this (less-nested) namespace.
                "reflectionConfiguration": {
                    "namespaceTemplates": [REFLECTION_NAMESPACE],
                },
            }
        }
    ]

    # create_or_get_memory creates the resource and waits until it's ACTIVE, or — if a
    # memory with this name already exists — returns that existing memory dict instead of
    # raising. Either way we get back a dict carrying the resource "id".
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # Episodic strategy => long-term episode extraction
        description="Long-term episodic memory for the Claude SDK debugging-assistant tutorial",
        event_expiry_days=7,  # Retain raw events for 7 days (configurable 3-365)
        # NOTE: no memory_execution_role_arn — built-in strategies don't need one.
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with EPISODIC strategy ready: {memory_id}")
    return memory_id


# ## Step 3b: Verify the configured strategy is actually EPISODIC
#
# The whole premise of this tutorial is "use the REAL episodic strategy." So we verify it
# at runtime instead of trusting the request we sent. `get_memory_strategies` returns the
# strategies as the service stored them; we assert at least one reports an episodic type.
# (The control plane reports the strategy `type` as the enum-style value `EPISODIC`.)


def verify_episodic_strategy(memory_id: str) -> None:
    """Confirm the memory actually has an Episodic strategy configured (not Semantic)."""
    try:
        strategies = memory_client.get_memory_strategies(memory_id)
    except Exception as e:
        logger.warning(f"Could not verify strategies (continuing): {e}")
        return

    types = [s.get("type") or s.get("memoryStrategyType") or s.get("strategyType") for s in strategies]
    logger.info(f"Configured strategy types: {types}")

    # Log the namespaces the SERVICE actually stored for the episodic strategy. The SDK
    # injects a session-scoped default `namespaces` when only `namespaceTemplates` is
    # passed, so this read-back confirms episodes/reflections land where retrieve_episodes
    # looks (`/episodes/{actorId}/`). A mismatch here — not "nothing extracted" — would be
    # the reason retrieval returns zero, so we surface it up front rather than after the
    # long wait.
    for s in strategies:
        stype = s.get("type") or s.get("memoryStrategyType") or s.get("strategyType")
        if stype and "EPISODIC" in str(stype).upper():
            episode_ns = s.get("namespaceTemplates") or []
            reflection_ns = s.get("reflectionConfiguration") or {}
            reflection_ns = reflection_ns.get("namespaceTemplates") or []
            logger.info(f"   Episode namespaces (as stored):    {episode_ns}")
            logger.info(f"   Reflection namespaces (as stored): {reflection_ns}")
            logger.info(f"   Retrieval will query (resolved):   {EPISODIC_NAMESPACE.format(actorId=ACTOR_ID)}")

    if any(t and "EPISODIC" in str(t).upper() for t in types):
        logger.info("✅ Verified: an EPISODIC strategy is configured on this memory")
    else:
        # Don't crash — but make the discrepancy loud, since this is the exact bug the
        # tutorial is meant to avoid.
        logger.warning(
            "⚠️ No EPISODIC strategy detected on this memory (found %s). "
            "If you reused an existing memory created with a different strategy, "
            "delete it and re-run so the Episodic strategy is created fresh.",
            types,
        )


# ## Step 4: Run a debugging session (one episode)
#
# Each *session* is one episode. Within a session we run several turns — mirroring the
# back-and-forth of a real debugging session (symptom → hypothesis → isolation → fix) —
# keeping the `messages[]` array by hand, exactly like the semantic tutorial. The
# difference is purely in how AgentCore processes the stored events: the Episodic strategy
# groups the turns of a session into a coherent episode.
#
# We accumulate the (text, role) tuples for the whole session and write them with a SINGLE
# `create_event` per session. `create_event` accepts the full ordered list of turns, and
# grouping a session's turns in one event gives the episodic pipeline a clean,
# self-contained sequence to consolidate into one episode.


def extract_text(response) -> str:
    """Concatenate all text blocks from a Claude response.

    A Messages API response `.content` is a list of content blocks; we only want the
    text blocks (this assistant defines no tools, so there are no tool_use blocks).
    """
    return "".join(block.text for block in response.content if block.type == "text")


def run_debugging_session(
    memory_id: str,
    session_id: str,
    user_turns: list,
    system_prompt: str,
    tool_note: Optional[str] = None,
    closing_user_turn: Optional[str] = None,
) -> list:
    """Run one multi-turn debugging session and persist it as a single episode.

    Args:
        memory_id: The AgentCore memory resource ID.
        session_id: Unique ID for THIS session — this is what scopes the episode.
        user_turns: Ordered list of user utterances that make up the session.
        system_prompt: The system prompt (base, or memory-enriched on resume).
        tool_note: Optional text persisted as a single ``TOOL`` turn after the first
            exchange. The episodic extractor's prompt is built around tool usage,
            arguments, and reasoning, and AWS guidance says including ``TOOL`` results
            "yields optimal results" — so we seed one representative tool observation
            (e.g. a metrics/log read) into the persisted transcript. This assistant
            defines no real tools, so the note is added to the *persisted* transcript
            only; it is NOT sent to Claude (which would require a matching tool_use
            block), keeping the Messages API call valid.
        closing_user_turn: Optional final USER utterance appended to the persisted
            transcript to signal the episode has concluded (e.g. "Thanks, that
            resolved it. The session is complete."). The extractor decides an episode
            is complete per-turn "by considering messages in the next turns"; its rule
            is that with no following turn and no clear conclusion signal, the episode
            stays "in progress." Ending on an explicit USER closure gives the detector
            an unambiguous completion signal. Like ``tool_note`` this is added to the
            persisted transcript only, not the live Claude conversation.

    Returns:
        The full (text, role) transcript that was persisted, for inspection.
    """
    print(f"\n--- Session '{session_id}' (one episode) ---")
    messages: list = []  # local conversation state for THIS session
    transcript: list = []  # (text, role) tuples we will persist as one event

    for i, user_text in enumerate(user_turns):
        # 1. Append the user's message to local conversation state.
        messages.append({"role": "user", "content": user_text})
        print(f"User:  {user_text}")

        # 2. Call Claude with the full session history.
        response = claude.messages.create(
            model=MODEL_ID,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )

        # 3. Extract the reply and append it so the next turn has full context.
        assistant_text = extract_text(response)
        messages.append({"role": "assistant", "content": assistant_text})
        print(f"Agent: {assistant_text}\n")

        # 4. Record both turns for the episode transcript.
        transcript.append((user_text, "USER"))
        transcript.append((assistant_text, "ASSISTANT"))

        # 4b. After the first exchange, optionally seed a representative TOOL turn into
        #     the persisted transcript (see ``tool_note`` docstring). Persisted only.
        if tool_note and i == 0:
            transcript.append((tool_note, "TOOL"))
            print(f"Tool:  {tool_note}\n")

    # 4c. Optionally append an explicit closing USER turn so the episodic extractor sees
    #     a clear "inquiry has concluded" signal — FOLLOWED by a confirming ASSISTANT turn.
    #     The extractor decides completion per-turn "by considering messages in the next
    #     turns"; a closing USER turn with NO following turn can still read as "in progress"
    #     (rule: no next turn + no clear conclusion => not an episode end). Appending a short
    #     ASSISTANT acknowledgement AFTER the closing USER turn gives the detector the
    #     "next turn" it needs to mark the episode complete. Verified empirically: episodes
    #     extract with the trailing ASSISTANT turn, and do not without it. Persisted only.
    if closing_user_turn:
        transcript.append((closing_user_turn, "USER"))
        print(f"User:  {closing_user_turn}\n")
        closing_ack = "Glad I could help — the root cause and fix are recorded. Closing this debugging session."
        transcript.append((closing_ack, "ASSISTANT"))
        print(f"Agent: {closing_ack}\n")

    # 5. Persist the WHOLE session as one event. Because the memory has the Episodic
    #    strategy, this multi-turn event is asynchronously consolidated into one episode
    #    (and later folded into cross-episode reflections). Writing the session's turns in
    #    a single create_event keeps the episode boundary clean: one session => one event
    #    => one episode.
    try:
        memory_client.create_event(
            memory_id=memory_id,
            actor_id=ACTOR_ID,
            session_id=session_id,
            messages=transcript,
        )
        logger.info(f"✅ Stored session '{session_id}' ({len(transcript)} turns) for episode extraction")
    except Exception as e:
        # Don't crash the demo if a single write fails; log and continue.
        logger.error(f"Memory save error for session '{session_id}': {e}")

    return transcript


# ## Step 5: Retrieve past episodes
#
# `retrieve_memories` runs a semantic search over a namespace and returns the most
# relevant records. The namespace must be FULLY RESOLVED — wildcards are not supported,
# so we substitute `{actorId}` ourselves. For the Episodic strategy, the returned records
# are episodes (and cross-episode reflections), each carrying its summary in
# `content.text`.


def retrieve_episodes(memory_id: str, query: str, top_k: int = 5) -> list:
    """Retrieve past episodes relevant to a query from the resolved episodes namespace."""
    namespace = EPISODIC_NAMESPACE.format(actorId=ACTOR_ID)
    try:
        records = memory_client.retrieve_memories(
            memory_id=memory_id,
            namespace=namespace,
            query=query,
            top_k=top_k,
        )
    except Exception as e:
        logger.error(f"Failed to retrieve episodes from {namespace}: {e}")
        return []

    texts = []
    for record in records:
        # Record shape: {"content": {"text": "..."}, "score": 0.87, ...}
        content = record.get("content", {})
        text = content.get("text", "").strip() if isinstance(content, dict) else ""
        if text:
            texts.append(text)
    logger.info(f"✅ Retrieved {len(texts)} episode record(s) from {namespace}")
    return texts


def build_episode_enriched_prompt(memory_id: str, query: str) -> str:
    """Fetch past episodes and fold them into a system prompt for a new session.

    This is how a stateless Claude agent gains episodic recall across sessions: we pull
    the consolidated episodes (and reflections) relevant to the current problem and
    prepend them as context. The agent then knows not just *facts*, but *what happened
    last time* — which past sessions are relevant, how they were resolved, and what
    patterns recur.
    """
    episodes = retrieve_episodes(memory_id, query)

    if not episodes:
        # Nothing extracted yet — fall back to the base prompt.
        return SYSTEM_PROMPT

    context_lines = [
        "",
        "Relevant past debugging sessions (episodic memory) — use these to recall what "
        "happened last time and avoid repeating earlier investigation:",
    ]
    for i, episode in enumerate(episodes, 1):
        context_lines.append(f"- [Past episode {i}] {episode}")
    context_lines.append(
        "When a current problem resembles a past episode, reference how it was resolved and apply the lesson learned."
    )

    return SYSTEM_PROMPT + "\n".join(context_lines)


# ## Step 6: Wait for the episodic pipeline
#
# Episodic extraction is asynchronous and runs THREE steps (Extraction → Consolidation →
# Reflection), so it takes longer than a single-step semantic extraction. We poll the
# episodes namespace until records appear or we hit the cap. In production you would not
# block like this; you'd retrieve on the developer's next session (typically minutes or
# days later), by which point the pipeline has long completed.


def wait_for_episodes(memory_id: str) -> None:
    """Poll until episode records appear, or until the max wait elapses."""
    logger.info(
        "⏳ Waiting up to %d min for asynchronous episodic extraction (Extraction → Consolidation → Reflection)...",
        EXTRACTION_MAX_WAIT_SECONDS // 60,
    )
    start = time.time()
    deadline = start + EXTRACTION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        episodes = retrieve_episodes(memory_id, query="debugging session", top_k=1)
        if episodes:
            logger.info("✅ Episode records are available after %ds", int(time.time() - start))
            return
        elapsed = int(time.time() - start)
        remaining = int(deadline - time.time())
        logger.info("   …no episodes yet (elapsed %ds, ~%ds remaining); polling again", elapsed, max(remaining, 0))
        time.sleep(EXTRACTION_POLL_INTERVAL_SECONDS)
    logger.warning(
        "⚠️ Episodes did not surface within the %d-min wait window. The episodic pipeline "
        "(especially Reflection) can take 10–15 min — try re-running retrieval later.",
        EXTRACTION_MAX_WAIT_SECONDS // 60,
    )


# ## Step 7: Run the demo
#
# We run TWO distinct debugging sessions (episodes), wait for the episodic pipeline, then
# start a THIRD session whose system prompt is enriched with the retrieved episodes. The
# third session demonstrates episodic recall: faced with a new-but-similar bug, the agent
# recalls *what happened last time* rather than starting cold.


# Session 1 (episode A): a memory leak caused by an unbounded cache.
SESSION_1_TURNS = [
    "Our payment service's memory usage climbs steadily after the latest deploy until the pod OOM-kills. Where do I start?",
    "The deploy added a new in-process caching layer for currency-conversion rates.",
    "You're right — the cache has no eviction. Entries are keyed by request ID, so it grows unbounded. I'll add a TTL and a max size.",
    "Confirmed fixed in v2.4.1: memory is now flat. The root cause was the unbounded cache with no TTL.",
]
# A representative tool observation for episode A (persisted as a TOOL turn). The episodic
# extractor is built around tool usage/arguments/reasoning, and TOOL results "yield optimal
# results" per AWS guidance.
SESSION_1_TOOL_NOTE = (
    "[tool: read_metrics(service='payment', metric='container_memory_rss_bytes', window='6h')] "
    "RSS rises monotonically from 180Mi to 1.4Gi over 6h with no plateau; GC pauses normal; "
    "heap-object histogram shows currency-rate cache entries dominating retained set."
)
# An explicit closing USER turn that signals the inquiry has concluded, so the episodic
# completion detector sees an unambiguous end-of-episode signal as the final turn.
SESSION_1_CLOSING = "Thanks, that resolved my issue. The session is complete."

# Session 2 (episode B): intermittent API timeouts under load.
SESSION_2_TURNS = [
    "The checkout service intermittently times out calling the inventory API, but only under load. Logs show no errors.",
    "Connection pool size is 10 and we run 50 concurrent workers. Latency spikes correlate with traffic peaks.",
    "Makes sense — workers are starving on connections. I raised the pool to 64 and added a 2s acquire timeout with retries.",
    "Resolved. Timeouts disappeared after sizing the connection pool to the worker count.",
]
SESSION_2_TOOL_NOTE = (
    "[tool: query_traces(service='checkout', op='inventory.call', filter='duration>2s', window='1h')] "
    "Slow spans cluster at traffic peaks; time is spent in 'connection-acquire' (~1.9s), not in the "
    "downstream call (~40ms); active connections pinned at pool max of 10 while 50 workers run."
)
SESSION_2_CLOSING = "Perfect, that fixed it. We can close this out — the session is complete."


def main() -> None:
    memory_id = None  # Initialize so the finally-block cleanup never hits a NameError
    try:
        memory_id = get_or_create_memory("ClaudeSDKEpisodicMemory")
        verify_episodic_strategy(memory_id)

        # ---- Episode A: first debugging session ---------------------------------
        # A unique session_id scopes this episode. Each session => one episode.
        print("\n=== Debugging Session 1 (seeding episodic memory) ===")
        run_debugging_session(
            memory_id,
            session_id="debug_session_memory_leak",
            user_turns=SESSION_1_TURNS,
            system_prompt=SYSTEM_PROMPT,
            tool_note=SESSION_1_TOOL_NOTE,
            closing_user_turn=SESSION_1_CLOSING,
        )

        # ---- Episode B: second, unrelated debugging session ---------------------
        print("\n=== Debugging Session 2 (seeding episodic memory) ===")
        run_debugging_session(
            memory_id,
            session_id="debug_session_api_timeout",
            user_turns=SESSION_2_TURNS,
            system_prompt=SYSTEM_PROMPT,
            tool_note=SESSION_2_TOOL_NOTE,
            closing_user_turn=SESSION_2_CLOSING,
        )

        # ---- Wait for the episodic pipeline -------------------------------------
        wait_for_episodes(memory_id)

        # ---- Inspect the extracted episodes -------------------------------------
        print("\n=== Extracted Episodes (episodic memory) ===")
        for episode in retrieve_episodes(memory_id, "past debugging sessions and their resolutions"):
            print(f"  • {episode}")
        print()

        # ---- Episode C: a NEW session that benefits from episodic recall --------
        # The new problem (steadily growing memory) resembles episode A. We retrieve past
        # episodes relevant to this problem and inject them into the system prompt, so the
        # agent recalls "what happened last time" — the unbounded-cache root cause — rather
        # than investigating from scratch. This is episodic recall ("what happened") vs.
        # semantic recall ("what facts do I know").
        print("=== Debugging Session 3 (new session, episodic memory injected) ===")
        new_problem = (
            "A different service — the notifications worker — is now slowly leaking memory "
            "after we added a per-recipient template cache. Memory climbs until it crashes. "
            "Have we seen anything like this before, and how should I approach it?"
        )
        enriched_prompt = build_episode_enriched_prompt(memory_id, query=new_problem)
        run_debugging_session(
            memory_id,
            session_id="debug_session_notifications_leak",
            user_turns=[new_problem],
            system_prompt=enriched_prompt,
        )

    finally:
        # ## Cleanup
        #
        # AgentCore Memory resources are billable, so we delete the resource when the
        # demo finishes. To keep the memory between runs (e.g. to inspect episodes in the
        # console, or to let Reflection finish), comment out the block below —
        # `get_or_create_memory` will reuse it by name.
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info(f"✅ Deleted memory: {memory_id}")
            except Exception as e:
                logger.error(f"Failed to delete memory {memory_id}: {e}")


if __name__ == "__main__":
    main()
