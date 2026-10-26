#!/usr/bin/env python

# # Claude SDK with AgentCore Memory (Long-Term Memory — Custom Strategy Override)
#
#
# ## Introduction
#
# This tutorial demonstrates how to build a **conversational agent** using the
# **Anthropic Claude SDK** (via Amazon Bedrock) with AgentCore **long-term memory**
# powered by a **built-in strategy with overrides** (also called a *custom strategy
# override*). The companion tutorial `01-built-in-strategies` used the default
# AgentCore-managed extraction pipeline. Here we keep that same pipeline and fixed
# output schema, but override two things:
#
#   - the **model** used for each step (extraction / consolidation), and
#   - the **prompt instructions** appended to that step's system prompt.
#
# This is the middle rung of customization. Built-in strategies (tutorial 01) give you
# zero-config extraction with AgentCore-managed models. Self-managed strategies (the far
# end — see `02-long-term-memory/03-self-managed-strategy`) hand you the entire pipeline
# via S3 + SNS + your own Lambda. Overrides sit in between: you keep AgentCore's managed
# extraction loop and its fixed record schema, but you steer *what* it extracts and
# *which* model does the work.
#
# Because the Anthropic SDK is a **stateless API client** with NO built-in conversation
# management or hooks, the integration is fully explicit. We:
#   1. create an IAM execution role (REQUIRED for overrides — Bedrock bills your account),
#   2. create a memory resource with a CUSTOM strategy wrapping a `semanticOverride`,
#      supplying our own extraction/consolidation models and prompt addenda,
#   3. drive a conversation, persisting each turn with `create_event` (the same call used
#      for built-in strategies — the override only changes how extraction runs),
#   4. wait for the asynchronous extraction to run with OUR model + prompt,
#   5. retrieve the custom-extracted records with `retrieve_memories`, and
#   6. inject them into the system prompt of a *new* session so the agent recalls the
#      user across conversations.
#
# **NOTE:** Built-in *with overrides* strategies REQUIRE an IAM execution role
# (`memory_execution_role_arn`). AgentCore assumes that role to invoke the Bedrock model
# you specified, and the invocations bill against YOUR account and quotas. This is the
# key operational difference from the plain built-in strategies in tutorial 01.
#
#
# ### Tutorial Details
#
# | Information         | Details                                                                          |
# |:--------------------|:---------------------------------------------------------------------------------|
# | Tutorial type       | Long-term Conversational                                                         |
# | Agent type          | Healthcare Intake Assistant                                                      |
# | Agentic Framework   | Anthropic Claude SDK (no framework)                                              |
# | LLM model           | Anthropic Claude Sonnet 4.6 (via Amazon Bedrock)                                 |
# | Tutorial components | AgentCore Custom Strategy Override (semanticOverride: extraction + consolidation), create_event, retrieve_memories |
# | Example complexity  | Advanced                                                                         |
#
# You'll learn to:
# - Create the IAM execution role that override strategies require
# - Create a memory resource with a `customMemoryStrategy` wrapping a `semanticOverride`
# - Supply your own `modelId` and `appendToPrompt` for extraction AND consolidation
# - Call Claude through Amazon Bedrock using the `AnthropicBedrock` client
# - Store each turn with `create_event` — extraction now runs with YOUR model + prompt
# - Retrieve the domain-scoped records with `retrieve_memories` and inject them downstream
#
# ## When to use overrides vs. built-in strategies
#
# Reach for a custom strategy override when the *default* extraction doesn't fit, but you
# still want AgentCore to run the pipeline for you:
#
# - **Domain-specific extraction.** You only care about a narrow slice of the conversation
#   (clinical facts, financial events, legal entities) and want everything else ignored.
#   This tutorial's healthcare intake agent is exactly this case — extract medications,
#   allergies, and conditions; ignore chit-chat.
# - **Compliance / model pinning.** Regulation or policy requires a specific, audited model
#   version for any LLM that touches user data, or you must keep all inference in-region.
# - **Different language or tone.** The user base speaks a language (or jargon) the default
#   prompt doesn't handle well, and you want extraction instructions in that language.
# - **Custom consolidation rules.** You need explicit rules for how new records merge with
#   old ones (e.g. "a severe allergy supersedes a mild one"; "prefer the most recent dose").
# - **Cost / latency tuning.** Swap in Haiku for high-volume, low-margin extraction or
#   Sonnet for nuanced consolidation — independently per step.
#
# Stick with **plain built-in strategies** (tutorial 01) when the defaults are fine: it's
# simpler and needs no IAM role. Graduate to a **self-managed strategy** only when you need
# a record schema the built-ins can't produce, a non-Bedrock model, or external lookups
# before deciding what to store — overrides cannot change the record shape, only the
# instructions and the model.
#
# ## Architecture
#
# ```
#   ┌──────────────┐                              ┌─────────────────────────────────────┐
#   │  Your code   │ ──── 1. create_event ──────▶ │  AgentCore Memory                   │
#   │ (messages[]) │       (each turn)            │                                     │
#   │              │                              │  short-term events ──┐              │
#   │              │                              │                      │              │
#   │              │                              │   2. async extraction│              │
#   │              │                              │      via YOUR model  ▼              │
#   │              │                              │      + YOUR prompt  ┌──────────────┐│
#   │              │                              │      (semanticOverr-│ Bedrock model││
#   │              │                              │       ide)          │ in your acct ││
#   │              │                              │         ▲           └──────┬───────┘│
#   │              │                              │         │ assumes          │        │
#   │              │                              │   memoryExecutionRoleArn   ▼        │
#   │              │ ◀─── 3. retrieve_memories ── │              long-term records      │
#   │              │       (resolved namespace)   │              (custom-extracted)     │
#   └──────┬───────┘                              └─────────────────────────────────────┘
#          │
#          │ 4. inject records into system prompt, then messages.create(...)
#          ▼
#   ┌──────────────┐
#   │ Claude via   │  5. assistant reply, now grounded in custom-extracted long-term memory
#   │ Amazon       │
#   │ Bedrock      │
#   └──────────────┘
# ```
#
# ## Prerequisites
#
# To execute this tutorial you will need:
# - Python 3.10+
# - AWS credentials with AgentCore Memory permissions, Amazon Bedrock model access, AND
#   IAM permissions to create a role (iam:CreateRole, iam:PutRolePolicy, iam:GetRole) —
#   or pass an existing role ARN via the MEMORY_EXECUTION_ROLE_ARN environment variable.
# - Access to the Claude Sonnet 4.6 model in Amazon Bedrock (request it in the Bedrock
#   console under Model access, in your chosen region). The override model you pick must
#   also be accessible to the execution role.
#
# Let's get started by setting up our environment!

# ## Step 1: Setup and Imports


# Run: pip install -qr requirements.txt


import json
import logging
import os
import time
from datetime import datetime

# The Anthropic SDK's Amazon Bedrock client. `pip install "anthropic[bedrock]"`
# provides this; it signs requests with your AWS credentials (SigV4) and speaks the
# Messages API against Bedrock — no Anthropic API key required.
from anthropic import AnthropicBedrock

# boto3 is used to create the IAM execution role that override strategies require.
import boto3

# AgentCore Memory client, the StrategyType enum (gives us the exact strategy keys),
# and the AWS error type we handle during memory creation.
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.constants import StrategyType
from botocore.exceptions import ClientError

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("claude-sdk-ltm-override")

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")  # AWS region for Bedrock, Memory, and IAM
ACTOR_ID = "patient_456"  # Any unique identifier for the end-user / agent
SESSION_ID = "intake_session_001"  # Unique identifier for the first conversation

# Model ID for Claude Sonnet 4.6 on Amazon Bedrock.
# The `global.` prefix selects the global (cross-region) inference endpoint, which is
# the default for Sonnet 4.6 and carries no regional pricing premium. To pin traffic
# to a region instead, swap the prefix (e.g. "us.anthropic.claude-sonnet-4-6").
MODEL_ID = "global.anthropic.claude-sonnet-4-6"

# The model AgentCore should use for the OVERRIDDEN extraction and consolidation steps.
# This is the heart of the override: instead of AgentCore's managed default, the
# extraction pipeline invokes THIS model (in your account, via the execution role).
# We use the same Sonnet 4.6 endpoint here, but you can pick any Bedrock model the
# execution role can invoke — e.g. Haiku for cheaper high-volume extraction.
OVERRIDE_MODEL_ID = os.getenv("OVERRIDE_MODEL_ID", "global.anthropic.claude-sonnet-4-6")

# Namespace template for the custom semantic records. `{actorId}` is substituted at
# extraction time, keeping every patient's clinical facts isolated. We retrieve from
# the resolved path (see Step 6).
CLINICAL_NAMESPACE = "/patients/{actorId}/clinical-facts/"

# Custom prompt addenda. `appendToPrompt` is ADDED to AgentCore's built-in system prompt
# for each step — it should NARROW or CLARIFY the default behavior, not contradict it.
# The output record schema is fixed; only these instructions and the model change.
EXTRACTION_ADDENDUM = (
    "You are extracting clinical intake facts. Capture ONLY health-relevant information: "
    "current medications and dosages, drug or food allergies and their severity, active "
    "diagnoses and chronic conditions, and relevant family medical history. Ignore "
    "scheduling chit-chat, pleasantries, and any non-clinical content."
)
CONSOLIDATION_ADDENDUM = (
    "When a new clinical fact relates to an existing record, prefer the more recent and "
    "more specific information. A more severe allergy supersedes a milder one for the "
    "same substance; an updated dosage supersedes an older dosage for the same medication."
)

# Long-term extraction is asynchronous — records appear ~30-90s after create_event.
# Override strategies can take a little longer (an extra model hop), so we cap generously.
EXTRACTION_MAX_WAIT_SECONDS = 150
EXTRACTION_POLL_INTERVAL_SECONDS = 15

SYSTEM_PROMPT = (
    "You are a careful, friendly healthcare intake assistant. You help patients record "
    "their medical background before an appointment. Be concise and professional, confirm "
    "what you heard, and never give medical advice or diagnoses. "
    f"Today's date: {datetime.today().strftime('%Y-%m-%d')}."
)

# Name of the IAM execution role we create (or reuse) for override model invocation.
EXECUTION_ROLE_NAME = "AgentCoreMemoryOverrideExecutionRole"


# ## Step 2: Initialize the Claude (Bedrock) and Memory clients
#
# The `AnthropicBedrock` client resolves AWS credentials the same way boto3 does
# (environment variables, shared `~/.aws/credentials`, or an instance/role profile).
# We only need to tell it which region to call.


claude = AnthropicBedrock(aws_region=REGION)
memory_client = MemoryClient(region_name=REGION)
logger.info(f"✅ Clients initialized for region: {REGION}")


# ## Step 3: Create the IAM Execution Role (REQUIRED for overrides)
#
# Unlike plain built-in strategies, an override strategy makes AgentCore invoke a Bedrock
# model *in your account*. To allow that, you create an IAM role AgentCore can assume and
# pass its ARN as `memory_execution_role_arn` when creating the memory. The role needs:
#
# - a **trust policy** letting `bedrock-agentcore.amazonaws.com` assume it (scoped to your
#   account + region), and
# - a **permissions policy** allowing `bedrock:InvokeModel` /
#   `bedrock:InvokeModelWithResponseStream` on the override model.
#
# These policies mirror the AWS documentation for built-in-with-overrides strategies. If
# you already have a suitable role, set MEMORY_EXECUTION_ROLE_ARN and we'll use it as-is.


def create_memory_execution_role() -> str:
    """Create (or reuse) the IAM role AgentCore assumes to invoke the override model.

    Returns the role ARN. Honors the MEMORY_EXECUTION_ROLE_ARN env var if set, so you
    can supply a pre-existing role and skip IAM writes entirely.
    """
    preset = os.getenv("MEMORY_EXECUTION_ROLE_ARN")
    if preset:
        logger.info(f"✅ Using execution role from MEMORY_EXECUTION_ROLE_ARN: {preset}")
        return preset

    iam = boto3.client("iam", region_name=REGION)
    account_id = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    role_arn = f"arn:aws:iam::{account_id}:role/{EXECUTION_ROLE_NAME}"

    # Trust policy: only the AgentCore Memory service, only for this account + region.
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "",
                "Effect": "Allow",
                "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:*"},
                },
            }
        ],
    }

    # Permissions policy: invoke Bedrock models, scoped to your own account's resources.
    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    "arn:aws:bedrock:*:*:inference-profile/*",
                ],
                "Condition": {"StringEquals": {"aws:ResourceAccount": account_id}},
            }
        ],
    }

    try:
        # Reuse the role if it already exists.
        try:
            iam.get_role(RoleName=EXECUTION_ROLE_NAME)
            logger.info(f"✅ IAM execution role already exists: {role_arn}")
            return role_arn
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise

        logger.info(f"Creating IAM execution role: {EXECUTION_ROLE_NAME}")
        iam.create_role(
            RoleName=EXECUTION_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore Memory custom strategy overrides",
            Tags=[{"Key": "Purpose", "Value": "AgentCoreMemoryOverride"}],
        )
        iam.put_role_policy(
            RoleName=EXECUTION_ROLE_NAME,
            PolicyName="AgentCoreMemoryBedrockAccess",
            PolicyDocument=json.dumps(permissions_policy),
        )
        logger.info(f"✅ Created IAM execution role: {role_arn}")
        # IAM is eventually consistent; give the role a moment to propagate before
        # AgentCore tries to assume it during memory creation.
        time.sleep(10)
        return role_arn
    except ClientError as e:
        if e.response["Error"]["Code"] == "AccessDenied":
            logger.error(
                "❌ Access denied creating the IAM role. Either grant iam:CreateRole / "
                "iam:PutRolePolicy / iam:GetRole, or set MEMORY_EXECUTION_ROLE_ARN to an "
                "existing role and re-run."
            )
        else:
            logger.error(f"❌ Failed to create IAM role: {e}")
        raise


# ## Step 4: Create the Memory Resource (with a custom strategy override)
#
# This is the key difference from tutorial 01. There we attached a plain
# `semanticMemoryStrategy`. Here we attach a `customMemoryStrategy` whose configuration
# wraps a `semanticOverride`. Each step (`extraction`, `consolidation`) takes:
#
# - `appendToPrompt` — instructions added to that step's built-in system prompt, and
# - `modelId` — the Bedrock model AgentCore invokes for that step (billed to your account).
#
# The override keeps the built-in semantic record SCHEMA — only the instructions and the
# model change. `create_or_get_memory` blocks until the resource is ACTIVE, reuses an
# existing memory of the same name, and (unlike tutorial 01) we MUST pass
# `memory_execution_role_arn`.


def get_or_create_memory(name: str, execution_role_arn: str) -> str:
    """Create a memory with a custom semantic override strategy, or reuse it if it exists."""
    # A single-key dict keyed by the CUSTOM strategy's wire value
    # (StrategyType.CUSTOM.value == "customMemoryStrategy"). The `semanticOverride` block
    # carries our extraction + consolidation overrides. This is the exact shape the SDK's
    # `add_custom_semantic_strategy` helper builds, and what the AWS docs document for
    # `CreateMemory`.
    strategies = [
        {
            StrategyType.CUSTOM.value: {
                "name": "ClinicalFactsOverride",
                "description": "Semantic extraction overridden for clinical intake facts",
                "namespaceTemplates": [CLINICAL_NAMESPACE],
                "configuration": {
                    "semanticOverride": {
                        "extraction": {
                            "appendToPrompt": EXTRACTION_ADDENDUM,
                            "modelId": OVERRIDE_MODEL_ID,
                        },
                        "consolidation": {
                            "appendToPrompt": CONSOLIDATION_ADDENDUM,
                            "modelId": OVERRIDE_MODEL_ID,
                        },
                    }
                },
            }
        }
    ]

    # `create_or_get_memory` creates the memory (waiting until ACTIVE) or, on a name
    # clash, returns the existing memory dict — so we don't hand-roll the reuse scan.
    # It passes `memory_execution_role_arn` straight through, which overrides require.
    memory = memory_client.create_or_get_memory(
        name=name,
        strategies=strategies,  # customMemoryStrategy => override pipeline is enabled
        description="Long-term memory for the Claude SDK custom-strategy-override tutorial",
        event_expiry_days=7,  # Retain raw events for 7 days (configurable 3-365)
        memory_execution_role_arn=execution_role_arn,  # REQUIRED for overrides
    )
    memory_id = memory["id"]
    logger.info(f"✅ Memory with custom override strategy ready: {memory_id}")
    return memory_id


# ## Step 5: The conversation turn
#
# Each turn mirrors tutorial 01: append the user message, call Claude with the full local
# history, append the reply, then persist the exchange with `create_event`. The override
# changes nothing on the WRITE path — `create_event` is identical. The difference is purely
# on AgentCore's side: extraction now runs with our model and our prompt addendum.


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

    # 4. Persist this exchange. Because the memory has a custom override strategy, this
    #    event is asynchronously processed into long-term records by OUR model + prompt.
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
        logger.info("✅ Stored turn (queued for custom-override extraction)")
    except Exception as e:
        # Don't crash the conversation if a single write fails; log and continue.
        logger.error(f"Memory save error: {e}")

    return assistant_text


# ## Step 6: Retrieve long-term memories
#
# `retrieve_memories` runs a semantic search over a namespace and returns the most
# relevant records. The namespace must be FULLY RESOLVED — wildcards are not supported,
# so we substitute `{actorId}` ourselves. Each returned record is a dict whose
# `content.text` holds the extracted memory and `score` holds the relevance. The records
# here were produced by our override model under our extraction prompt.


def retrieve_long_term(memory_id: str, namespace_template: str, query: str, top_k: int = 5) -> list:
    """Retrieve custom-extracted long-term records from one resolved namespace."""
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
    """Fetch custom-extracted records and fold them into a system prompt for a new session.

    This is how a stateless Claude agent gets 'memory' across conversations: we pull the
    distilled clinical facts and prepend them as context, so even a brand-new `messages[]`
    array carries what the agent learned previously.
    """
    facts = retrieve_long_term(memory_id, CLINICAL_NAMESPACE, query)
    if not facts:
        # Nothing extracted yet — fall back to the base prompt.
        return SYSTEM_PROMPT

    context_lines = ["", "Clinical background you have on file for this patient:"]
    for fact in facts:
        context_lines.append(f"- {fact}")
    context_lines.append(
        "Use this context to avoid re-asking what you already know. Do not provide diagnoses or medical advice."
    )
    return SYSTEM_PROMPT + "\n".join(context_lines)


# ## Step 7: Wait for extraction
#
# Long-term extraction is asynchronous — records do not exist the instant `create_event`
# returns, and an override adds an extra model hop. We poll the clinical namespace until
# records appear or we hit the cap. In production you would not block like this; you'd
# retrieve on the next user interaction (typically minutes later) by which point
# extraction has long completed.


def wait_for_extraction(memory_id: str) -> None:
    """Poll until custom-extracted records appear, or until the max wait elapses."""
    logger.info("⏳ Waiting for asynchronous custom-override extraction to complete...")
    namespace = CLINICAL_NAMESPACE.format(actorId=ACTOR_ID)
    deadline = time.time() + EXTRACTION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            records = memory_client.retrieve_memories(
                memory_id=memory_id,
                namespace=namespace,
                query="patient clinical facts",
                top_k=1,
            )
            if records:
                logger.info("✅ Custom-extracted records are available")
                return
        except Exception as e:
            logger.warning(f"Retrieval probe failed (will retry): {e}")
        time.sleep(EXTRACTION_POLL_INTERVAL_SECONDS)
    logger.warning(
        "⚠️ Extraction did not surface records within the wait window. Override strategies "
        "invoke Bedrock in your account — if records never appear, check that the execution "
        "role can invoke the model and that you are not being throttled (enable memory log "
        "delivery to see ingestion errors)."
    )


# ## Step 8: Run the demo
#
# We run a first conversation (which seeds memory), wait for extraction, then start a
# SECOND session with a fresh `messages[]` array. The second session's system prompt is
# enriched with the custom-extracted records retrieved from the first — so the agent
# recalls the patient's clinical background despite having no short-term history loaded.


def main() -> None:
    memory_id = None  # Initialize so the finally-block cleanup never hits a NameError
    try:
        # ---- Required IAM execution role for overrides --------------------------
        execution_role_arn = create_memory_execution_role()

        memory_id = get_or_create_memory("ClaudeSDKLongTermOverride", execution_role_arn)

        # ---- First conversation: seed long-term memory --------------------------
        # The override extraction prompt keeps clinical facts and drops the small talk.
        print("\n=== First Conversation (seeding long-term memory) ===")
        messages: list = []
        for user_text in [
            "Hi, thanks for fitting me in! By the way, traffic was awful this morning.",
            "I take metformin 500mg twice daily for type 2 diabetes, and lisinopril 10mg "
            "once a day for blood pressure.",
            "I'm severely allergic to penicillin — it gives me hives and trouble breathing. "
            "Also, my mom had breast cancer in her early 50s.",
        ]:
            print(f"User:  {user_text}")
            reply = chat_turn(memory_id, messages, user_text, SYSTEM_PROMPT)
            print(f"Agent: {reply}\n")

        # ---- Wait for the extraction pipeline -----------------------------------
        wait_for_extraction(memory_id)

        # ---- Inspect the custom-extracted records -------------------------------
        # Expect clinical facts (medications, allergy, family history). The traffic
        # small-talk should NOT appear — the override extraction prompt suppresses it.
        print("=== Custom-Extracted Long-Term Memory (clinical facts) ===")
        for fact in retrieve_long_term(memory_id, CLINICAL_NAMESPACE, "medications, allergies, conditions"):
            print(f"  • {fact}")
        print("(The 'traffic was awful' small talk should NOT appear — the override drops it.)\n")

        # ---- Second session: new state, memory injected into the prompt ---------
        # Simulate the patient returning later. We start with an EMPTY messages[] array
        # (no short-term history) and instead enrich the system prompt with the custom
        # records retrieved above. If extraction worked, the agent already knows the
        # patient's medications and allergies.
        print("=== Second Session (new process, long-term memory injected) ===")
        follow_up = "I'm here for a follow-up. Can you confirm what you have on file for me?"
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
        #
        # NOTE: we intentionally do NOT delete the IAM execution role here — it's cheap,
        # reusable across runs, and may be shared. Delete it manually if you no longer
        # need it (detach the inline policy, then delete the role).
        if memory_id:
            try:
                memory_client.delete_memory_and_wait(memory_id=memory_id)
                logger.info(f"✅ Deleted memory: {memory_id}")
            except Exception as e:
                logger.error(f"Failed to delete memory {memory_id}: {e}")


if __name__ == "__main__":
    main()
