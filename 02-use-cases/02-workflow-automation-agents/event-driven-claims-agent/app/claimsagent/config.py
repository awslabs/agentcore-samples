"""Centralized configuration. ALL env var reads live here — nowhere else.

Environment variables are injected by the CDK stack at deploy time.
The L3 construct auto-generates names like AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL
and MEMORY_CLAIMSAGENTMEMORY_ID. We read both the auto-generated and explicit names.
"""

import os

# ─── Model ──────────────────────────────────────────────────────────────────
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "global.anthropic.claude-sonnet-4-6")
# Fast/cheap model for the Validation Agent (classification task, no tool use).
FAST_MODEL_ID = os.getenv("FAST_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")

# ─── AWS Region ─────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-west-2")

# ─── Gateway ────────────────────────────────────────────────────────────────
# The L3 construct sets AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL automatically.
# We also check the explicit name passed by infra-construct for backward compat.
GATEWAY_URL = os.getenv(
    "AGENTCORE_GATEWAY_URL",
    os.getenv("AGENTCORE_GATEWAY_CLAIMSGATEWAY_URL", ""),
)
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "agentcore/invoke")

# Use the exact v1 provider binding. Never fall back to the potentially shared
# bare v0 provider when local configuration is incomplete.
GATEWAY_CREDENTIAL_PROVIDER = os.getenv(
    "AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER", os.getenv("CREDENTIAL_COGNITO_GATEWAY_M2M_NAME", "")
)

# ─── Memory ─────────────────────────────────────────────────────────────────
# L3 construct injects MEMORY_CLAIMSAGENTMEMORY_ID; explicit fallback for manual config.
MEMORY_ID = os.getenv(
    "MEMORY_CLAIMSAGENTMEMORY_ID",
    os.getenv("AGENTCORE_MEMORY_ID", ""),
)

# Memory retrieval tuning — controls how much prior context is recalled per invocation.
MEMORY_RETRIEVAL_TOP_K = int(os.getenv("MEMORY_RETRIEVAL_TOP_K", "5"))
MEMORY_RETRIEVAL_RELEVANCE = float(os.getenv("MEMORY_RETRIEVAL_RELEVANCE", "0.5"))

# ─── Routing ────────────────────────────────────────────────────────────────
# Confidence score threshold for auto-approval. Claims with confidence >= this
# value are approved automatically; below routes to human review.
AUTO_APPROVE_THRESHOLD = int(os.getenv("AUTO_APPROVE_THRESHOLD", "80"))

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
