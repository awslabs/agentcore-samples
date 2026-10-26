# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""MCP target types: the gateway's aggregated ``/mcp`` endpoint and HTTP
passthrough targets that are themselves MCP servers (``/<targetName>/mcp``).

Both are the base :class:`TargetType` behavior (live passthrough + a
path-inserted PRM); they differ only in the viewer path and the origin path.
"""

from __future__ import annotations

from .base import TargetType


class McpTarget(TargetType):
    """The gateway's managed, aggregated MCP endpoint (``/mcp``)."""

    type_key = "mcp"
    requires_target_name = False
    description = "Gateway aggregated MCP endpoint (/mcp)"

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        return f"{route_prefix}/mcp"

    def _origin_prefix(self, target_name: str | None) -> str:
        return "/mcp"


class HttpMcpTarget(TargetType):
    """An HTTP passthrough target that is itself an MCP server
    (``/<targetName>/mcp``)."""

    type_key = "http_mcp"
    requires_target_name = True
    description = "HTTP passthrough MCP target (/<targetName>/mcp)"

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        return f"{route_prefix}/{target_name}/mcp"

    def _origin_prefix(self, target_name: str | None) -> str:
        return f"/{target_name}/mcp"
