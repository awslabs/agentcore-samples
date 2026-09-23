# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Configuration settings for AgentCore Identity Sample.
All sensitive values are loaded from environment variables or AWS Secrets Manager.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# Load .env file from project root
from dotenv import load_dotenv

# Find project root (look for .env file)
current_dir = Path(__file__).resolve().parent
project_root = current_dir.parent.parent  # shared/config -> shared -> project_root
env_file = project_root / ".env"

if env_file.exists():
    load_dotenv(env_file)
else:
    # Try current working directory
    load_dotenv()

# Import secrets provider (lazy to avoid circular imports)
if TYPE_CHECKING:
    from .secrets_provider import SecretsProvider

logger = logging.getLogger(__name__)


@dataclass
class Auth0Config:
    """Auth0 configuration settings."""

    domain: str = field(default_factory=lambda: os.getenv("AUTH0_DOMAIN", ""))
    client_id: str = field(default_factory=lambda: os.getenv("AUTH0_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("AUTH0_CLIENT_SECRET", ""))
    audience: str = field(default_factory=lambda: os.getenv("AUTH0_AUDIENCE", "https://agentcore-financial-api"))
    callback_url: str = field(default_factory=lambda: os.getenv("AUTH0_CALLBACK_URL", "http://localhost:9090/callback"))

    # Custom claims namespace
    claims_namespace: str = "https://agentcore.example.com/"

    # OAuth scopes — fine-grained resource-level scopes for demo visibility
    scopes: str = (
        "openid profile email "
        "profile:personal:read profile:personal:write "
        "profile:preferences:read profile:preferences:write "
        "accounts:savings:read accounts:savings:write "
        "accounts:transaction:read "
        "accounts:credit:read accounts:credit:write "
        "accounts:investment:read"
    )

    @classmethod
    def from_env(cls) -> "Auth0Config":
        """Load Auth0 configuration from environment variables."""
        return cls(
            domain=os.getenv("AUTH0_DOMAIN", ""),
            client_id=os.getenv("AUTH0_CLIENT_ID", ""),
            client_secret=os.getenv("AUTH0_CLIENT_SECRET", ""),
            audience=os.getenv("AUTH0_AUDIENCE", "https://agentcore-financial-api"),
            callback_url=os.getenv("AUTH0_CALLBACK_URL", "http://localhost:9090/callback"),
        )

    @classmethod
    def from_secrets_provider(
        cls,
        provider: Optional["SecretsProvider"] = None,
        secret_name: Optional[str] = None,
    ) -> "Auth0Config":
        """
        Load Auth0 configuration from a secrets provider.

        Args:
            provider: Optional SecretsProvider instance. If None, uses default.
            secret_name: Optional secret name. Defaults to SECRET_NAME_AUTH0 env var
                        or "agentcore/auth0".

        Returns:
            Auth0Config instance with credentials from secrets provider.

        Falls back to environment variables if secrets provider fails.
        """
        from .secrets_provider import (
            SecretsProviderError,
            get_default_provider,
        )

        if provider is None:
            provider = get_default_provider()

        if secret_name is None:
            secret_name = os.getenv("SECRET_NAME_AUTH0", "agentcore/auth0")

        try:
            secrets = provider.get_secret(secret_name)
            return cls(
                domain=secrets.get("domain", ""),
                client_id=secrets.get("client_id", ""),
                client_secret=secrets.get("client_secret", ""),
                audience=secrets.get(
                    "audience",
                    os.getenv("AUTH0_AUDIENCE", "https://agentcore-financial-api"),
                ),
                callback_url=os.getenv("AUTH0_CALLBACK_URL", "http://localhost:9090/callback"),
            )
        except SecretsProviderError as e:
            logger.warning(f"Failed to load Auth0 config from secrets provider: {e}")
            logger.info("Falling back to environment variables")
            return cls.from_env()

    @property
    def authorization_url(self) -> str:
        return f"https://{self.domain}/authorize"

    @property
    def token_url(self) -> str:
        return f"https://{self.domain}/oauth/token"

    @property
    def userinfo_url(self) -> str:
        return f"https://{self.domain}/userinfo"

    @property
    def jwks_url(self) -> str:
        return f"https://{self.domain}/.well-known/jwks.json"

    @property
    def issuer(self) -> str:
        return f"https://{self.domain}/"


@dataclass
class AgentCoreConfig:
    """AgentCore configuration settings."""

    region: str = field(default_factory=lambda: os.getenv("AWS_REGION", "us-east-1"))

    # Runtime configuration
    coordinator_agent_id: str = field(default_factory=lambda: os.getenv("COORDINATOR_AGENT_ID", ""))
    profile_agent_id: str = field(default_factory=lambda: os.getenv("PROFILE_AGENT_ID", ""))
    accounts_agent_id: str = field(default_factory=lambda: os.getenv("ACCOUNTS_AGENT_ID", ""))

    # Identity configuration
    identity_pool_id: str = field(default_factory=lambda: os.getenv("AGENTCORE_IDENTITY_POOL_ID", ""))
    jwt_authorizer_id: str = field(default_factory=lambda: os.getenv("AGENTCORE_JWT_AUTHORIZER_ID", ""))

    # Memory configuration
    memory_id: str = field(default_factory=lambda: os.getenv("AGENTCORE_MEMORY_ID", ""))

    # Gateway configuration
    gateway_url: str = field(default_factory=lambda: os.getenv("AGENTCORE_GATEWAY_URL", ""))


@dataclass
class AppConfig:
    """Application-level configuration."""

    debug: bool = field(default_factory=lambda: os.getenv("DEBUG", "false").lower() == "true")
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # Streamlit configuration
    streamlit_port: int = field(default_factory=lambda: int(os.getenv("STREAMLIT_PORT", "8501")))

    # OAuth callback server
    oauth_callback_port: int = field(default_factory=lambda: int(os.getenv("OAUTH_CALLBACK_PORT", "9090")))
    oauth_callback_host: str = field(default_factory=lambda: os.getenv("OAUTH_CALLBACK_HOST", "localhost"))


def _create_auth0_config() -> Auth0Config:
    """Factory function to create Auth0Config using secrets provider."""
    return Auth0Config.from_secrets_provider()


@dataclass
class Settings:
    """Main settings container."""

    auth0: Auth0Config = field(default_factory=_create_auth0_config)
    agentcore: AgentCoreConfig = field(default_factory=AgentCoreConfig)
    app: AppConfig = field(default_factory=AppConfig)

    def validate(self) -> list[str]:
        """Validate required settings are present."""
        errors = []

        if not self.auth0.domain:
            errors.append("AUTH0_DOMAIN is required")
        if not self.auth0.client_id:
            errors.append("AUTH0_CLIENT_ID is required")
        if not self.auth0.client_secret:
            errors.append("AUTH0_CLIENT_SECRET is required")

        return errors

    def refresh_secrets(self) -> None:
        """
        Refresh Auth0 secrets from the secrets provider.

        Useful for responding to secret rotation events or
        when you need to force a refresh of cached credentials.
        """
        from .secrets_provider import get_default_provider

        provider = get_default_provider()
        secret_name = os.getenv("SECRET_NAME_AUTH0", "agentcore/auth0")

        # Clear the cache and refresh
        provider.clear_cache()
        self.auth0 = Auth0Config.from_secrets_provider(provider, secret_name)
        logger.info("Auth0 secrets refreshed from provider")


# Global settings instance
settings = Settings()
