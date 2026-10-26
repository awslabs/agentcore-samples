# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""HTTP agent target type: a normal agent exposed as a tool (``/<targetName>``)
with **no** agent card.

Like :class:`~custom_domains.targets.a2a.A2aTarget` in path shape, but it does
not serve or rewrite an A2A agent card — so it is the plain base
:class:`TargetType` behavior (live passthrough + a path-inserted PRM + the
``WWW-Authenticate`` rewrite). Use this for an agent that is invoked as a tool
and does not publish ``/.well-known/agent-card.json``; use ``http_a2a`` when the agent
does publish a card that must be rewritten to the custom domain.
"""

from __future__ import annotations

from .base import TargetType


class HttpAgentTarget(TargetType):
    """An HTTP passthrough agent-as-tool with no agent card (``/<targetName>``)."""

    type_key = "http_agent"
    requires_target_name = True
    description = "HTTP agent-as-tool, no card (/<targetName>)"

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        return f"{route_prefix}/{target_name}"

    def _origin_prefix(self, target_name: str | None) -> str:
        return f"/{target_name}"
