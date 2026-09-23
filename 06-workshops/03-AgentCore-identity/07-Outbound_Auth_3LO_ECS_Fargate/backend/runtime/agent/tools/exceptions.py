# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared exceptions for agent tools."""


class AuthorizationRequiredError(Exception):
    """Raised when OAuth authorization is required for any provider."""

    def __init__(self, provider: str, auth_url: str) -> None:
        """Initialize with provider name and authorization URL."""
        self.provider = provider
        self.auth_url = auth_url
        super().__init__(f"Please authorize {provider} access: {auth_url}")


class ApiError(Exception):
    """Raised when external API call fails."""

    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        """Initialize with provider, message, and optional status code."""
        self.provider = provider
        self.status_code = status_code
        msg = f"{provider} API error: {message}"
        if status_code:
            msg += f" (HTTP {status_code})"
        super().__init__(msg)
