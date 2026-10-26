# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Middleware for tool/resource/prompt scoped authorization via JWT claims."""

import copy
import logging
from collections.abc import Callable

from fastmcp.exceptions import FastMCPError, PromptError, ResourceError, ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.utilities.components import FastMCPComponent
from mcp.types import PaginatedRequest
from src.auth.utils import ROLES_META_KEY, SCOPES_META_KEY, get_access_token
from src.exceptions import AuthError

logger = logging.getLogger(__name__)


class AuthMiddleware(Middleware):
    """Enforces role/scope-based access on tools, resources, and prompts."""

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        return await self._authorize_list(context, call_next)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        return await self._authorize_execute(
            context,
            call_next,
            get_component=lambda ctx: ctx.fastmcp_context.fastmcp.get_tool(ctx.message.name),
            error_cls=ToolError,
        )

    async def _authorize_list(self, context, call_next):
        try:
            token = get_access_token()
        except AuthError:
            logger.warning("No access token -> security trimming all components")
            return []

        results = await call_next(context)
        return [self._strip_meta(r) for r in results if not self._should_trim(r, token.roles, token.scopes)]

    async def _authorize_execute(self, context, call_next, get_component: Callable, error_cls: type[FastMCPError]):
        try:
            token = get_access_token()
        except AuthError:
            raise error_cls("Access denied")

        component = await get_component(context)
        if self._should_trim(component, token.roles, token.scopes):
            logger.warning(
                "Access denied to tool",
                extra={"tool": getattr(component, "name", "unknown"), "roles": token.roles, "scopes": token.scopes},
            )
            raise error_cls("Access denied")

        logger.info(
            "Tool execution authorized",
            extra={"tool": getattr(component, "name", "unknown"), "roles": token.roles, "scopes": token.scopes},
        )
        return await call_next(context)

    def _should_trim(self, component: FastMCPComponent, roles: list[str], scopes: list[str]) -> bool:
        """Decide whether to hide a component from the caller.

        A gated component declares the credentials that grant it — roles, scopes, or both
        (see auth_meta()). The caller is authorized if it presents *any* of them:

          - user tokens satisfy the role requirement via the custom:roles claim
          - machine (client_credentials) tokens satisfy the scope requirement

        There is deliberately no blanket bypass for machine callers: a machine caller
        reaches only what its granted scopes name, and a newly added gated tool is denied
        until a scope is explicitly granted in the CDK stack. Deny-by-default matters most
        for tools added later, which must not become reachable simply by shipping.
        """
        meta = component.meta or {}
        required_roles = meta.get(ROLES_META_KEY) or []
        required_scopes = meta.get(SCOPES_META_KEY) or []

        if not required_roles and not required_scopes:
            return False  # ungated component

        if required_roles and any(r in roles for r in required_roles):
            return False
        if required_scopes and any(s in scopes for s in required_scopes):
            return False

        return True

    def _strip_meta(self, component: FastMCPComponent) -> FastMCPComponent:
        """Remove internal auth metadata (Roles/Scopes) before returning to caller."""
        meta = component.meta or {}
        if ROLES_META_KEY not in meta and SCOPES_META_KEY not in meta:
            return component
        c = copy.copy(component)
        c.meta = {k: v for k, v in meta.items() if k not in {ROLES_META_KEY, SCOPES_META_KEY}}
        return c
