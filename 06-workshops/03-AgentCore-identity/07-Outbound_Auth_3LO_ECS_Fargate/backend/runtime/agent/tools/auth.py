# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Authentication decorator for AgentCore identity.

This module provides a custom requires_access_token decorator as an alternative to
the official bedrock_agentcore.identity.auth decorator:
https://github.com/aws/bedrock-agentcore-sdk-python/blob/main/src/bedrock_agentcore/identity/auth.py
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html

Why not use the official decorator directly?
- The official decorator relies on contextvars (BedrockAgentCoreContext) to obtain the
  workload_access_token, which requires using BedrockAgentCoreApp as your server.
- This implementation accepts workload_access_token as an explicit parameter, making it
  usable with any server framework (FastAPI, Starlette, etc.) without implicit context.
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from bedrock_agentcore.services.identity import IdentityClient, TokenPoller

logger = logging.getLogger(__name__)


def requires_access_token(
    *,
    provider_name: str,
    scopes: list[str],
    auth_flow: Literal["M2M", "USER_FEDERATION"],
    workload_access_token: str | None = None,
    session_binding_url: str | None = None,
    on_auth_url: Callable[[str], Any] | None = None,
    force_authentication: bool = False,
    token_poller: TokenPoller | None = None,
    custom_state: str | None = None,
    custom_parameters: dict[str, str] | None = None,
    into: str = "access_token",
    region: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Fetch OAuth2 access token with explicit workload token.

    Args:
        provider_name: The credential provider name
        scopes: OAuth2 scopes to request
        auth_flow: Authentication flow type ("M2M" or "USER_FEDERATION")
        workload_access_token: The workload access token (explicit, not from context)
        session_binding_url: Session Binding URL pointing to the customer-managed service that completes the session binding
        on_auth_url: Handler invoked with the authorization URL when user authorization is required
        force_authentication: Force re-authentication
        token_poller: Custom token poller implementation
        custom_state: State for callback verification
        custom_parameters: Additional OAuth parameters
        into: Parameter name to inject the token into
        region: AWS region

    Returns:
        Decorator function

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        client = IdentityClient(region)

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                if not workload_access_token:
                    raise ValueError("workload_access_token is required")
                token = await client.get_token(
                    provider_name=provider_name,
                    agent_identity_token=workload_access_token,
                    scopes=scopes,
                    auth_flow=auth_flow,
                    callback_url=session_binding_url,
                    on_auth_url=on_auth_url,
                    force_authentication=force_authentication,
                    token_poller=token_poller,
                    custom_state=custom_state,
                    custom_parameters=custom_parameters,
                )
                kwargs[into] = token
                return await func(*args, **kwargs)
            except Exception:
                logger.exception("Error in requires_access_token decorator")
                raise

        return wrapper

    return decorator
