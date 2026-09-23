# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""GitHub agent tools for AgentCore Identity."""

import logging
from typing import Any

import httpx
from strands import tool

from backend.runtime.agent.tools.auth import requires_access_token
from backend.runtime.agent.tools.exceptions import ApiError, AuthorizationRequiredError
from backend.runtime.agent.tools.github.config import GitHubConfig
from backend.runtime.agent.tools.github.models import GitHubProject, GitHubUser

logger = logging.getLogger(__name__)


class GitHubTools:
    """Tools for interacting with GitHub using OAuth authentication."""

    def __init__(self, config: GitHubConfig) -> None:
        """Initialize GitHub tools.

        Args:
            config: GitHub configuration object

        """
        self.config = config

    def _on_auth_url(self, url: str) -> None:
        """Handle authorization URL by raising AuthorizationRequiredError.

        This URL must be presented to the user to grant access.
        """
        raise AuthorizationRequiredError(provider="GitHub", auth_url=url)

    async def _call_github_api(self, endpoint: str, scopes: list[str], params: dict | None = None) -> Any:
        """Make authenticated GitHub API call.

        Raises:
            ApiError: When API call fails

        """

        @requires_access_token(
            provider_name=self.config.provider_name,
            scopes=scopes,
            auth_flow="USER_FEDERATION",
            workload_access_token=self.config.workload_access_token,
            session_binding_url=self.config.session_binding_url,
            on_auth_url=self._on_auth_url,
            region=self.config.aws_region,
        )
        async def make_request(*, access_token: str) -> Any:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.config.github_api_base}{endpoint}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params=params or {},
                    timeout=10.0,
                )
                response.raise_for_status()
                return response.json()

        try:
            return await make_request()
        except httpx.HTTPStatusError as e:
            logger.exception("GitHub API error: %s", e.response.status_code)
            raise ApiError(
                provider="GitHub",
                message="API request failed",
                status_code=e.response.status_code,
            ) from e

    @tool
    async def get_github_user(self) -> GitHubUser:
        """Get the authenticated GitHub user's profile information.

        Use this tool when the user wants to:
        - See their GitHub profile
        - Check who they are authenticated as
        - View their GitHub account details

        Returns:
            GitHub user profile

        Raises:
            ApiError: When API call fails

        """
        result: dict[str, Any] = await self._call_github_api("/user", scopes=["read:user"])
        return GitHubUser.model_validate(result)

    @tool
    async def list_github_repos(self, limit: int = 10) -> list[GitHubProject]:
        """List the user's GitHub repositories.

        Use this tool when the user wants to:
        - See their GitHub repositories
        - List repos they have access to
        - Browse their GitHub workspace
        - Find a specific repository

        Args:
            limit: Maximum number of repositories to return (default: 10)

        Returns:
            List of GitHub repositories

        Raises:
            ApiError: When API call fails

        """
        result = await self._call_github_api(
            "/user/repos",
            scopes=["repo"],
            params={"per_page": limit, "sort": "updated"},
        )

        return [GitHubProject.model_validate(p) for p in result]
