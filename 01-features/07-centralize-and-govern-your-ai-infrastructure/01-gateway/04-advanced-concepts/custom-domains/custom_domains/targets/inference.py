# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Inference target type: the gateway's aggregated ``/inference`` endpoint.

Analogous to :class:`~custom_domains.targets.mcp.McpTarget` — a gateway-level
endpoint (no ``target_name``), differing only in the path (``/inference`` instead
of ``/mcp``). It is the base :class:`TargetType` behavior: live passthrough + a
path-inserted PRM + the ``WWW-Authenticate`` rewrite.
"""

from __future__ import annotations

from .base import TargetType


class InferenceTarget(TargetType):
    """The gateway's aggregated inference endpoint (``/inference``)."""

    type_key = "inference"
    requires_target_name = False
    description = "Gateway aggregated inference endpoint (/inference)"

    def _live_base(self, route_prefix: str, target_name: str | None) -> str:
        return f"{route_prefix}/inference"

    def _origin_prefix(self, target_name: str | None) -> str:
        return "/inference"
