"""Centralized configuration for the IT Incident Response Agent.

All environment variable resolution lives here. Other modules import from
this file instead of reading os.environ directly. This ensures consistent
precedence and a single place to document each variable.
"""

import os

# ─── Gateway ─────────────────────────────────────────────────────────────────
# The L3 construct injects the URL as AGENTCORE_GATEWAY_{NAME}_URL.
# We also accept GATEWAY_URL for backward compat and explicit override.
GATEWAY_URL = os.getenv("GATEWAY_URL") or os.getenv("AGENTCORE_GATEWAY_ITINCIDENTGATEWAY_URL", "")
GATEWAY_AUTH_MODE = os.getenv("GATEWAY_AUTH_MODE", "AWS_IAM")
GATEWAY_OAUTH_PROVIDER_NAME = os.getenv("GATEWAY_OAUTH_PROVIDER_NAME") or os.getenv("OAUTH_PROVIDER_NAME", "")
GATEWAY_OAUTH_AUDIENCE = os.getenv("GATEWAY_OAUTH_AUDIENCE") or os.getenv("GATEWAY_AUDIENCE", "")

# ─── Memory ──────────────────────────────────────────────────────────────────
# The L3 construct uses MEMORY_{NAME}_ID naming convention.
# Accept MEMORY_ID as an explicit override for backward compat / local dev.
MEMORY_ID = os.getenv("MEMORY_ID") or os.getenv("MEMORY_ITINCIDENTAGENTMEMORY_ID", "")

# ─── AWS Region ──────────────────────────────────────────────────────────────
REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-west-2")

# ─── DynamoDB ────────────────────────────────────────────────────────────────
TICKETS_TABLE = os.getenv("TICKETS_TABLE", "")

# ─── EventBridge ─────────────────────────────────────────────────────────────
EVENT_BUS_NAME = os.getenv("EVENT_BUS_NAME", "default")

# ─── Guardrail ───────────────────────────────────────────────────────────────
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

# ─── Jira Integration (opt-in) ───────────────────────────────────────────────
JIRA_MCP_URL = os.getenv("JIRA_MCP_URL")  # e.g. https://mcp.atlassian.com/v1/sse
JIRA_OAUTH_PROVIDER_NAME = os.getenv("JIRA_OAUTH_PROVIDER_NAME")
JIRA_SITE_URL = os.getenv("JIRA_SITE_URL", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "INC")

# ─── Model ───────────────────────────────────────────────────────────────────
AGENT_MODEL_ID = os.getenv("AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
FAST_MODEL_ID = os.getenv("FAST_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
