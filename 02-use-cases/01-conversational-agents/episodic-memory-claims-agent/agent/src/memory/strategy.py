"""
Custom episodic memory strategy — single source of truth.

Holds the custom extraction / consolidation / reflection prompts and the
model, plus helpers to build the strategy dict and create the memory resource.

Used by:
  - setup/0_setup_infra.sh  (initial infra)
  - memory/recreate.py      (repeatable recreate CLI)

Design notes (learned the hard way — see MEMORY_REDESIGN_PLAN.md):
  - The custom strategy steers extraction/reflection toward CLAIMS DECISIONING,
    not agent-orchestration mechanics.
  - modelId MUST be an inference profile (us./global.), not a bare model id.
  - Sonnet 4.0 is not available to the memory service in us-east-1; Sonnet 4.5 is.
  - Opus 4.x is rejected at runtime ("assistant message prefill") during
    consolidation — do NOT use Opus for memory extraction.
"""

import logging

from memory.config import EXTRACTION_MODEL_ID

logger = logging.getLogger("claims-demo.memory.strategy")

STRATEGY_NAME = "ClaimsEpisodes"
EPISODE_NAMESPACE_TEMPLATE = "claims/{actorId}/{sessionId}/"
REFLECTION_NAMESPACE_TEMPLATE = "claims/"

# ---------------------------------------------------------------------------
# Custom prompts (appended to the AWS base prompts for each step)
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = (
    "This conversation is an insurance claim processed by a multi-agent system (an "
    "orchestrator that routes to intake, investigation, and adjudication specialists). "
    "When forming the episode, focus ONLY on the claims-decisioning substance: incident "
    "type and circumstances; policy/coverage determination (covered vs excluded, and why); "
    "fraud indicators found and the resulting risk level; the final decision "
    "(APPROVE/DENY/ESCALATE), the amount, and the reasons; evidence/documentation that "
    "supported the decision. The final decision may be set by a HUMAN ADJUSTER, recorded as "
    "an '[ADJUSTER DECISION] APPROVE|DENY|ESCALATE — <notes>' turn near the end of the "
    "conversation. When such a turn is present, treat the adjuster's decision as the "
    "AUTHORITATIVE outcome of the claim (it overrides any agent recommendation), and record "
    "the adjuster's stated reasons. Explicitly IGNORE and do NOT record: how the orchestrator "
    "routed between agents, the order/sequencing of tool or sub-agent calls, conversational "
    "pleasantries, and how questions were relayed to the user. These orchestration mechanics "
    "are not the lesson; the claims reasoning and outcome are."
)

CONSOLIDATION_PROMPT = (
    "When consolidating claim episodes, preserve the claims-decisioning details (incident, "
    "coverage determination, fraud risk, decision, amount, reasons). Merge duplicates about "
    "the same claim. Do not retain agent-orchestration or routing details."
)

REFLECTION_PROMPT = (
    "Generalize patterns about INSURANCE CLAIMS DECISIONING across episodes: combinations of "
    "fraud signals that predict escalation or denial (e.g. delayed reporting + repeat claim "
    "type); when claims are approved vs escalated vs denied and the deciding factors; coverage "
    "gotchas and exclusion patterns by claim/policy type; repeat-claimant behavior and what it "
    "implies. Ground these patterns in the AUTHORITATIVE outcomes — when an episode's outcome "
    "was set by a human adjuster ([ADJUSTER DECISION]), weight that human judgment as the "
    "ground truth for the pattern. Do NOT produce insights about agent orchestration, pipeline "
    "sequencing, tool-call order, or how to converse with or gather information from users. "
    "Every reflection should help a claims adjuster decide a future claim more accurately."
)


INDEXED_KEYS = [
    {"key": "grounding_source", "type": "STRING"},
]

METADATA_SCHEMA = [
    {
        "key": "grounding_source",
        "type": "STRING",
        "extractionConfig": {
            "llmExtractionConfig": {
                "definition": (
                    "Whether this memory record is grounded in a human adjuster's "
                    "authoritative decision or based solely on automated agent assessment."
                ),
                "llmExtractionInstruction": (
                    "If the conversation or episodes contain an '[ADJUSTER DECISION]' turn "
                    "with an explicit APPROVE/DENY/ESCALATE decision made by a human adjuster, "
                    "classify as 'human_adjuster'. Otherwise classify as 'agent_only'."
                ),
                "validation": {"stringValidation": {"allowedValues": ["human_adjuster", "agent_only"]}},
            }
        },
    }
]


def build_strategy(model_id: str = EXTRACTION_MODEL_ID) -> dict:
    """Build the customMemoryStrategy dict for create_memory_and_wait."""
    return {
        "customMemoryStrategy": {
            "name": STRATEGY_NAME,
            "description": "Custom episodic strategy steered toward claims decisioning (not orchestration).",
            "namespaceTemplates": [EPISODE_NAMESPACE_TEMPLATE],
            "memoryRecordSchema": {"metadataSchema": METADATA_SCHEMA},
            "configuration": {
                "episodicOverride": {
                    "extraction": {"appendToPrompt": EXTRACTION_PROMPT, "modelId": model_id},
                    "consolidation": {"appendToPrompt": CONSOLIDATION_PROMPT, "modelId": model_id},
                    "reflection": {
                        "appendToPrompt": REFLECTION_PROMPT,
                        "modelId": model_id,
                        "namespaceTemplates": [REFLECTION_NAMESPACE_TEMPLATE],
                        "memoryRecordSchema": {"metadataSchema": METADATA_SCHEMA},
                    },
                }
            },
        }
    }


def create_claims_memory(
    client,
    name: str,
    memory_execution_role_arn: str,
    model_id: str = EXTRACTION_MODEL_ID,
    event_expiry_days: int = 30,
    max_wait: int = 300,
    poll_interval: int = 10,
) -> str:
    """Create the claims memory with the custom episodic strategy. Returns memory_id.

    If a memory with this name already exists, returns its id instead of failing.
    """
    from botocore.exceptions import ClientError

    strategies = [build_strategy(model_id)]
    try:
        memory = client.create_memory_and_wait(
            name=name,
            strategies=strategies,
            indexed_keys=INDEXED_KEYS,
            description="Insurance claims demo — custom episodic strategy (claims-focused, metadata-filtered)",
            event_expiry_days=event_expiry_days,
            memory_execution_role_arn=memory_execution_role_arn,
            max_wait=max_wait,
            poll_interval=poll_interval,
        )
        return memory["id"]
    except ClientError as e:
        if "already exists" in str(e):
            for m in client.list_memories():
                if m["id"].startswith(name):
                    return m["id"]
        raise
