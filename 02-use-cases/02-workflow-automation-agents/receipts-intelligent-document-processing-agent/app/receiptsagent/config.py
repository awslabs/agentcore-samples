"""Centralized configuration. ALL env var reads live here — nowhere else.

This module is the **replaceable deploy seam** (spec §13): the agent depends only
on environment variables / AppConfig, never on CLI- or CDK-specific assumptions.
Swapping the deploy mechanism (pure CDK, an SDK deploy, or a simpler setup) only
changes how these env vars get set, not the agent code.

Environment variables are injected by the CDK stack at deploy time. The
`@aws/agentcore-cdk` constructs auto-generate names like
`AGENTCORE_GATEWAY_RECEIPTSGATEWAY_URL` and `MEMORY_RECEIPTSAGENTMEMORY_ID`; we read
both the auto-generated and the explicit names for robustness.
"""

import os

# ─── Region ───────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION", "us-west-2")

# ─── Model / degradation ladder (spec §6) ──────────────────────────────────
# The model id is NOT a hardcoded constant. In a deployed stack it is resolved
# from the active degradation rung in AppConfig (see model/ladder.py). This
# env var is the default for local dev and the fallback if AppConfig is
# unreachable — it is the L0 rung's model.
DEFAULT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "global.anthropic.claude-opus-4-8")

# AppConfig coordinates for the ladder. When unset (local dev), the agent runs on
# DEFAULT_MODEL_ID with all features on — the ladder is a deployed-stack concern.
APPCONFIG_APPLICATION = os.getenv("APPCONFIG_APPLICATION", "")
APPCONFIG_ENVIRONMENT = os.getenv("APPCONFIG_ENVIRONMENT", "")
APPCONFIG_PROFILE = os.getenv("APPCONFIG_PROFILE", "")
# The AppConfig Agent Lambda extension serves cached config on localhost:2772.
APPCONFIG_EXTENSION_URL = os.getenv("APPCONFIG_EXTENSION_URL", "http://localhost:2772")

# ─── Gateway (MCP tool endpoint) ────────────────────────────────────────────
GATEWAY_URL = os.getenv(
    "AGENTCORE_GATEWAY_URL",
    os.getenv("AGENTCORE_GATEWAY_RECEIPTSGATEWAY_URL", ""),
)
GATEWAY_TOKEN_ENDPOINT = os.getenv("AGENTCORE_GATEWAY_TOKEN_ENDPOINT", "")
GATEWAY_OAUTH_SCOPES = os.getenv("AGENTCORE_GATEWAY_OAUTH_SCOPES", "")
GATEWAY_CLIENT_ID = os.getenv("AGENTCORE_GATEWAY_CLIENT_ID", "")
GATEWAY_CLIENT_SECRET = os.getenv("AGENTCORE_GATEWAY_CLIENT_SECRET", "")

# ─── Memory (spec §5.2; custom receipts/ namespaces) ────────────────────────
MEMORY_ID = os.getenv(
    "MEMORY_RECEIPTSAGENTMEMORY_ID",
    os.getenv("AGENTCORE_MEMORY_ID", ""),
)

# ─── L4 defer queue (spec §6.1/§12) ─────────────────────────────────────────
DEFER_QUEUE_URL = os.getenv("DEFER_QUEUE_URL", "")

# ─── Run-ledger event bus (operational audit) ───────────────────────────────
# The agent emits one event per run to this EventBridge bus; a writer Lambda upserts
# the ProcessingRuns table. Unset (local dev) = no emit, agent runs normally.
RUN_EVENT_BUS = os.getenv("RUN_EVENT_BUS", "")

# ─── Conversational identity (KMS HMAC, the IDOR fix) ───────────────────────
# In query mode the agent derives user_id from a token signed by THIS KMS key, never
# from the request body. The key never leaves KMS (GenerateMac/VerifyMac).
IDENTITY_KEY_ID = os.getenv("IDENTITY_KEY_ID", "")

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
