"""MCP Client for the Atlassian Remote MCP server (Jira integration).

Opt-in: Only activates when JIRA_MCP_URL is set. When absent, returns None
and the agent operates without Jira tools (DDB mock path).

Authentication uses AgentCore Identity with auth_flow="USER_FEDERATION"
(OAuth 2.0 3LO). The @requires_access_token decorator handles the token
fetch — it auto-detects async/sync context and manages the event loop
correctly. The agent never sees the client_secret.

First-time setup: Atlassian 3LO requires a real user to grant consent once.
On the first invocation, the on_auth_url callback logs the consent URL.
After consent, AgentCore caches the refresh token for all future invocations.
"""

import logging
from typing import Optional

from strands.tools.mcp.mcp_client import MCPClient
from config import JIRA_MCP_URL, JIRA_OAUTH_PROVIDER_NAME

logger = logging.getLogger(__name__)

# Atlassian 3LO scopes: read:me + read:jira-work for fetching issues;
# write:jira-work for commenting + transitioning; offline_access for refresh.
JIRA_SCOPES = [
    "read:me",
    "read:jira-user",
    "read:jira-work",
    "write:jira-work",
    "offline_access",
]


def _is_jira_configured() -> bool:
    """Check whether Jira integration env vars are present."""
    return bool(JIRA_MCP_URL and JIRA_OAUTH_PROVIDER_NAME)


def get_jira_mcp_client_sync() -> Optional[MCPClient]:
    """Returns an MCP Client connected to the Atlassian Remote MCP server.

    STEP: IDENTITY — Uses @requires_access_token(auth_flow="USER_FEDERATION")
    to fetch a 3LO token from AgentCore Identity. The decorator handles
    async/sync context detection automatically — no manual asyncio.run() needed.

    Returns None if:
      - JIRA_MCP_URL is not configured (opt-out)
      - Token fetch fails (consent not granted, provider misconfigured)

    Uses `prefix="jira"` to namespace Jira tools and avoid collisions
    with Gateway tools (e.g., both could expose a "search" tool).
    """
    if not _is_jira_configured():
        logger.info("Jira integration not configured (JIRA_MCP_URL unset) — skipping")
        return None

    from bedrock_agentcore.identity.auth import requires_access_token
    from mcp.client.sse import sse_client

    @requires_access_token(
        provider_name=JIRA_OAUTH_PROVIDER_NAME,
        auth_flow="USER_FEDERATION",
        scopes=JIRA_SCOPES,
        on_auth_url=lambda url: logger.warning("Atlassian consent required (one-time). Visit: %s", url),
    )
    def _build_client(*, access_token: str) -> MCPClient:
        """Decorated — token injected by @requires_access_token."""
        logger.info("Using Atlassian MCP client with configured provider")
        return MCPClient(
            lambda: sse_client(
                JIRA_MCP_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            ),
            prefix="jira",
        )

    try:
        # access_token is injected by the @requires_access_token decorator at call time;
        # pylint cannot see the decorator's signature transformation.
        return _build_client()  # pylint: disable=missing-kwoa
    except Exception as exc:
        logger.warning("Jira MCP client creation failed: %s (Jira tools unavailable)", exc)
        return None
