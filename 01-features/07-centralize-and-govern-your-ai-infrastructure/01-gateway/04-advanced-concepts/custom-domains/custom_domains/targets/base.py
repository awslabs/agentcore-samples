# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Target-type abstraction.

A *target type* describes how one endpoint attached to a gateway route is
projected onto the custom domain: which CloudFront paths serve it, how the
viewer URI maps to the gateway origin URI, and how the downstream discovery
document (OAuth Protected Resource Metadata or A2A Agent Card) must be
overridden so discovery resolves against the custom domain instead of the
raw gateway hostname.

This module is intentionally free of any ``aws_cdk`` import so it can be used
by both the ``agwcd`` CLI (validation / prompting) and the CDK stack
(synthesis). Every target type returns plain data (an :class:`EndpointPlan`);
the stack turns that data into CloudFront constructs.
"""

from __future__ import annotations

from dataclasses import dataclass

# RFC 9728 default well-known path for OAuth Protected Resource Metadata.
# Shared by the MCP and A2A targets (both advertise a per-endpoint PRM).
PRM_WELL_KNOWN = "/.well-known/oauth-protected-resource"


@dataclass(frozen=True)
class FunctionEntry:
    """One row of the CloudFront viewer-request function's routing table.

    The function strips ``prefix`` from the viewer URI and replaces it with
    ``origin_prefix`` (so the gateway receives the path it expects), and, when
    ``resource_metadata`` is set, injects it as the ``x-agwcd-resource-metadata``
    request header for the ``WWW-Authenticate`` rewrite to consume.
    """

    prefix: str
    origin_prefix: str
    resource_metadata: str | None = None


@dataclass(frozen=True)
class DiscoveryRoute:
    """A discovery document the regional discovery Lambda must synthesize.

    The Lambda matches ``path`` (exact), fetches ``downstream_url`` verbatim,
    and applies ``overrides`` according to ``kind`` (never building the document
    from scratch — only overriding the fields below).

    ``resource_metadata`` is the custom-domain PRM URL the Lambda advertises in a
    ``WWW-Authenticate`` challenge when the downstream fetch returns 401/403 (set
    on auth-protected discovery docs, e.g. the A2A agent card).
    """

    path: str
    kind: str  # "prm" | "agent_card"
    downstream_url: str
    overrides: dict[str, str]
    resource_metadata: str | None = None


@dataclass(frozen=True)
class EndpointPlan:
    """Everything the stack needs to wire up a single endpoint."""

    # CloudFront path patterns routed to the gateway origin (live traffic).
    live_patterns: list[str]
    # CloudFront path patterns routed to the discovery Lambda origin.
    discovery_patterns: list[str]
    # Viewer-request function routing-table row for the live paths.
    function_entry: FunctionEntry
    # Discovery documents to synthesize for the discovery Lambda's routes.json.
    discovery_routes: list[DiscoveryRoute]
    # Whether live behaviors need the WWW-Authenticate origin-response rewrite.
    needs_www_auth: bool = False


def _route_prefix(path: str) -> str:
    """Normalize a route path to a URI prefix ("" for root, else "/sales")."""
    if path in ("", "/"):
        return ""
    return "/" + path.strip("/")


def _gateway_base(gateway_url: str) -> str:
    """Gateway URL without a trailing slash."""
    return gateway_url.rstrip("/")


class TargetType:
    """Base class for target types.

    Every AgentCore Gateway endpoint sits behind the gateway's single inbound
    authorizer, so each one advertises OAuth discovery the same way: a
    path-inserted PRM (RFC 9728) whose ``resource`` is the endpoint's own
    custom-domain URL, proxied from the gateway's single root PRM, plus a
    ``WWW-Authenticate`` rewrite on the live endpoint. :meth:`plan` builds all of
    that. A concrete type only declares:

    * :meth:`_live_base` — the viewer path (e.g. ``/sales/mcp``).
    * :meth:`_origin_prefix` — the path the gateway expects (e.g. ``/mcp``).
    * :meth:`_extra_discovery_routes` — optional; extra discovery docs beyond the
      PRM (only the A2A agent card, today).

    So a new type is a small subclass plus one line in ``registry.py`` — nothing
    else in the codebase changes. A "normal agent as a tool" (no agent card) is
    just the base behavior: override ``_live_base``/``_origin_prefix`` and leave
    ``_extra_discovery_routes`` at its default.
    """

    #: Stable key used in the config file (``endpoints[].type``).
    type_key: str = ""
    #: Whether this target type requires a ``target_name`` (HTTP passthrough).
    requires_target_name: bool = False
    #: Human description shown by the CLI.
    description: str = ""

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        """Viewer path of the live endpoint (``route_prefix`` is "" for root)."""
        raise NotImplementedError

    def _origin_prefix(self, target_name: str | None) -> str:
        """Path the gateway origin expects (the viewer prefix is stripped to it)."""
        raise NotImplementedError

    def _extra_discovery_routes(
        self,
        *,
        domain_name: str,
        live_base: str,
        gateway_base: str,
        target_name: str | None,
        resource_metadata: str,
    ) -> list[DiscoveryRoute]:
        """Discovery docs beyond the PRM (default: none)."""
        return []

    def plan(
        self,
        *,
        domain_name: str,
        path: str,
        gateway_url: str,
        target_name: str | None = None,
    ) -> EndpointPlan:
        if self.requires_target_name and not target_name:
            raise ValueError(f"{self.type_key} target requires a target_name")

        route_prefix = _route_prefix(path)
        live_base = self._live_base(route_prefix, target_name)
        origin_prefix = self._origin_prefix(target_name)
        gateway_base = _gateway_base(gateway_url)

        # RFC 9728 path insertion: the PRM for resource https://<d><live_base>
        # lives at https://<d>/.well-known/oauth-protected-resource<live_base>,
        # and its `resource` MUST equal the URL the client used. The gateway
        # serves one root PRM (single inbound authorizer advertises the right
        # auth server for every path); we proxy it and override only `resource`.
        resource = f"https://{domain_name}{live_base}"
        prm_viewer = f"{PRM_WELL_KNOWN}{live_base}"
        resource_metadata = f"https://{domain_name}{prm_viewer}"
        prm_route = DiscoveryRoute(
            path=prm_viewer,
            kind="prm",
            downstream_url=f"{gateway_base}{PRM_WELL_KNOWN}",
            overrides={"resource": resource},
        )
        extra = self._extra_discovery_routes(
            domain_name=domain_name,
            live_base=live_base,
            gateway_base=gateway_base,
            target_name=target_name,
            resource_metadata=resource_metadata,
        )

        return EndpointPlan(
            live_patterns=[live_base, f"{live_base}/*"],
            discovery_patterns=[prm_viewer] + [r.path for r in extra],
            function_entry=FunctionEntry(
                prefix=live_base,
                origin_prefix=origin_prefix,
                resource_metadata=resource_metadata,
            ),
            discovery_routes=[prm_route] + extra,
            needs_www_auth=True,
        )
