"""
Memory configuration for the insurance claims demo.

The AgentCore memory_id is sourced from SSM Parameter Store
(`/insurance-claims-demo/memory_id`) as the single source of truth, so a
memory recreate only requires updating one SSM parameter — no redeploys.
config.json is kept as a convenience mirror / fallback for local dev.
"""

import json
import logging
import os

import boto3
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.models.filters import (
    MemoryMetadataFilter,
    MemoryRecordLeftExpression,
    MemoryRecordOperatorType,
    MemoryRecordRightExpression,
)
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger("claims-demo.memory")

# SSM parameter that holds the current memory_id (single source of truth)
MEMORY_ID_SSM_PARAM = "/insurance-claims-demo/memory_id"

# SSM parameter holding the system-wide decision mode: "auto" | "human"
#   auto  = agent uses memory reflections and finalizes the decision
#   human = agent gathers factual signals only (no memory), creates a review
#           task, and a human adjuster decides
DECISION_MODE_SSM_PARAM = "/insurance-claims-demo/decision_mode"
DEFAULT_DECISION_MODE = "auto"

# SSM parameter holding the HITL review-tasks DynamoDB table name (single source
# of truth; mirrored into config.json for local-dev fallback).
REVIEW_TASKS_TABLE_SSM_PARAM = "/insurance-claims-demo/review_tasks_table"

# SSM parameter holding the HITL reviews API base URL (API Gateway).
REVIEWS_API_URL_SSM_PARAM = "/insurance-claims-demo/reviews_api_url"

# ---------------------------------------------------------------------------
# Namespace templates — used when creating the memory and when retrieving
# ---------------------------------------------------------------------------
# Episodes are actor + session scoped (per-claim, contains PII).
# Reflections live at the strategy-level parent (cross-actor patterns, no PII).
# NOTE: the reflection namespace MUST be a prefix of the episode namespace
#       (AgentCore episodic strategy requirement).
EPISODE_NAMESPACE_TEMPLATE = "claims/{actorId}/{sessionId}/"
REFLECTION_NAMESPACE = "claims/"

# Metadata filter: only retrieve human-adjuster-grounded reflections (auto mode)
HUMAN_GROUNDED_FILTER = [
    MemoryMetadataFilter.build_expression(
        MemoryRecordLeftExpression.build("grounding_source"),
        MemoryRecordOperatorType.EQUALS_TO,
        MemoryRecordRightExpression.build_string("human_adjuster"),
    )
]


AGENT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"
EXTRACTION_MODEL_ID = "global.anthropic.claude-sonnet-4-6"


def episode_namespace_path(actor_id: str) -> str:
    """Hierarchical prefix for all of one policyholder's episodes (all sessions).

    Use with retrieve_memories(namespace_path=...) to pull this actor's prior
    claims without pulling other actors' data or reflections.
    """
    return f"claims/{actor_id}/"


# ---------------------------------------------------------------------------
# Config file path — checked in multiple locations for flexibility.
# On AgentCore Runtime, SSM is the source of truth (config.json is optional).
# ---------------------------------------------------------------------------
_CONFIG_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "setup", "config.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "setup", "config.json"),
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "setup", "config.json"),
    os.path.join(os.getcwd(), "setup", "config.json"),
]


def load_config() -> dict:
    """Load config.json if available, otherwise return minimal config from env/defaults.

    On AgentCore Runtime, config.json may not exist — SSM is the source of truth.
    The returned dict must have at minimum a 'region' key.
    """
    for candidate in _CONFIG_CANDIDATES:
        path = os.path.normpath(candidate)
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    # No config.json found — return defaults (SSM will be used for actual values)
    return {"region": os.environ.get("AWS_REGION", "us-east-1")}


def get_memory_client(config: dict | None = None) -> MemoryClient:
    """Return a MemoryClient for the configured region."""
    if config is None:
        config = load_config()
    return MemoryClient(region_name=config["region"])


def get_memory_id(config: dict | None = None) -> str:
    """Return the AgentCore memory ID.

    Source of truth is SSM (`/insurance-claims-demo/memory_id`). Falls back to
    config.json's `memory_id` if SSM is unavailable or the param is unset.
    """
    if config is None:
        config = load_config()
    region = config["region"]
    try:
        ssm = boto3.client("ssm", region_name=region)
        value = ssm.get_parameter(Name=MEMORY_ID_SSM_PARAM)["Parameter"]["Value"]
        if value:
            return value
    except (ClientError, BotoCoreError) as e:
        logger.warning(
            "Could not read %s from SSM (%s); falling back to config.json",
            MEMORY_ID_SSM_PARAM,
            e,
        )
    return config.get("memory_id", "")


def get_decision_mode(config: dict | None = None) -> str:
    """Return the system-wide decision mode: "auto" or "human".

    Source of truth is SSM (`/insurance-claims-demo/decision_mode`). Defaults to
    "auto" if the param is unset or SSM is unavailable.
    """
    if config is None:
        config = load_config()
    region = config["region"]
    try:
        ssm = boto3.client("ssm", region_name=region)
        value = ssm.get_parameter(Name=DECISION_MODE_SSM_PARAM)["Parameter"]["Value"]
        value = (value or "").strip().lower()
        if value in ("auto", "human"):
            return value
        logger.warning("Unexpected decision_mode %r in SSM; defaulting to %s", value, DEFAULT_DECISION_MODE)
    except (ClientError, BotoCoreError) as e:
        logger.warning(
            "Could not read %s from SSM (%s); defaulting to %s",
            DECISION_MODE_SSM_PARAM,
            e,
            DEFAULT_DECISION_MODE,
        )
    return DEFAULT_DECISION_MODE


def set_decision_mode(mode: str, config: dict | None = None) -> str:
    """Set the system-wide decision mode in SSM. Returns the normalized value."""
    mode = (mode or "").strip().lower()
    if mode not in ("auto", "human"):
        raise ValueError(f"decision_mode must be 'auto' or 'human', got {mode!r}")
    if config is None:
        config = load_config()
    ssm = boto3.client("ssm", region_name=config["region"])
    ssm.put_parameter(Name=DECISION_MODE_SSM_PARAM, Value=mode, Type="String", Overwrite=True)
    return mode


def get_review_tasks_table(config: dict | None = None) -> str:
    """Return the HITL review-tasks DynamoDB table name.

    Source of truth is SSM (`/insurance-claims-demo/review_tasks_table`). Falls
    back to config.json's `review_tasks_table` if SSM is unavailable or unset.
    """
    if config is None:
        config = load_config()
    region = config["region"]
    try:
        ssm = boto3.client("ssm", region_name=region)
        value = ssm.get_parameter(Name=REVIEW_TASKS_TABLE_SSM_PARAM)["Parameter"]["Value"]
        if value:
            return value
    except (ClientError, BotoCoreError) as e:
        logger.warning(
            "Could not read %s from SSM (%s); falling back to config.json",
            REVIEW_TASKS_TABLE_SSM_PARAM,
            e,
        )
    return config.get("review_tasks_table", "")


def get_reviews_api_url(config: dict | None = None) -> str:
    """Return the HITL reviews API base URL (API Gateway).

    Source of truth is SSM (`/insurance-claims-demo/reviews_api_url`). Falls back
    to config.json's `reviews_backend.api_url` if SSM is unavailable or unset.
    """
    if config is None:
        config = load_config()
    region = config["region"]
    try:
        ssm = boto3.client("ssm", region_name=region)
        value = ssm.get_parameter(Name=REVIEWS_API_URL_SSM_PARAM)["Parameter"]["Value"]
        if value:
            return value
    except (ClientError, BotoCoreError) as e:
        logger.warning(
            "Could not read %s from SSM (%s); falling back to config.json",
            REVIEWS_API_URL_SSM_PARAM,
            e,
        )
    return config.get("reviews_backend", {}).get("api_url", "")
