# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""A2A agent target type (HTTP passthrough to an Agent2Agent agent).

Same base behavior as an MCP endpoint (live passthrough + a path-inserted PRM),
plus one extra discovery doc: the agent card, served under the agent's route
with its ``url`` rewritten to the custom domain.
"""

from __future__ import annotations

from .base import DiscoveryRoute, TargetType

# A2A publishes the Agent Card at this well-known path (RFC 8615). We serve one
# per agent, scoped under the agent's route.
AGENT_CARD_WELL_KNOWN = "/.well-known/agent-card.json"


class A2aTarget(TargetType):
    """An HTTP passthrough target that is an A2A agent (``/<targetName>``)."""

    type_key = "http_a2a"
    requires_target_name = True
    description = "A2A agent (/<targetName>, card at agent-scoped well-known)"

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        return f"{route_prefix}/{target_name}"

    def _origin_prefix(self, target_name: str | None) -> str:
        return f"/{target_name}"

    def _extra_discovery_routes(
        self,
        *,
        domain_name: str,
        live_base: str,
        gateway_base: str,
        target_name: str | None,
        resource_metadata: str,
    ) -> list[DiscoveryRoute]:
        agent_url = f"https://{domain_name}{live_base}"
        # Agent-scoped well-known so multiple agents can coexist on one domain.
        card_viewer = f"{live_base}{AGENT_CARD_WELL_KNOWN}"
        # The target's agent card on the raw gateway; we override only its URLs.
        downstream_card = f"{gateway_base}/{target_name}{AGENT_CARD_WELL_KNOWN}"
        return [
            DiscoveryRoute(
                path=card_viewer,
                kind="agent_card",
                downstream_url=downstream_card,
                overrides={
                    # Override the agent's connection URL(s) only.
                    "url": agent_url,
                    # Prefix-swap any additionalInterfaces[].url that point at
                    # this target on the raw gateway.
                    "gateway_base": f"{gateway_base}/{target_name}",
                    "agent_base": agent_url,
                },
                # On a downstream 401 the discovery Lambda challenges the client
                # toward this agent's custom-domain PRM.
                resource_metadata=resource_metadata,
            )
        ]
