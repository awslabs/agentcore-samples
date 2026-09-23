# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Configuration management for AgentCore Identity."""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Auth0Config:
    """Auth0 authentication configuration."""

    domain: str
    client_id: str
    client_secret: str
    audience: str
    claims_namespace: str = "https://agentcore.example.com/"

    @property
    def issuer(self) -> str:
        """Get the issuer URL."""
        return f"https://{self.domain}/"

    @property
    def jwks_uri(self) -> str:
        """Get the JWKS URI."""
        return f"https://{self.domain}/.well-known/jwks.json"

    @classmethod
    def from_env(cls) -> "Auth0Config":
        """
        Load Auth0 configuration from environment variables.

        Expected environment variables:
        - AUTH0_DOMAIN
        - AUTH0_CLIENT_ID
        - AUTH0_CLIENT_SECRET
        - AUTH0_AUDIENCE
        - AUTH0_CLAIMS_NAMESPACE (optional)
        """
        return cls(
            domain=os.environ.get("AUTH0_DOMAIN", ""),
            client_id=os.environ.get("AUTH0_CLIENT_ID", ""),
            client_secret=os.environ.get("AUTH0_CLIENT_SECRET", ""),
            audience=os.environ.get("AUTH0_AUDIENCE", ""),
            claims_namespace=os.environ.get("AUTH0_CLAIMS_NAMESPACE", "https://agentcore.example.com/"),
        )


@dataclass
class AgentConfig:
    """Agent service configuration."""

    name: str
    port: int
    host: str = "0.0.0.0"
    debug: bool = False

    @classmethod
    def from_env(cls, agent_name: str) -> "AgentConfig":
        """
        Load agent configuration from environment variables.

        Args:
            agent_name: Name of the agent (e.g., "coordinator", "customer_profile")
        """
        prefix = agent_name.upper().replace("-", "_")

        return cls(
            name=agent_name,
            port=int(os.environ.get(f"{prefix}_PORT", "8000")),
            host=os.environ.get(f"{prefix}_HOST", "0.0.0.0"),
            debug=os.environ.get(f"{prefix}_DEBUG", "false").lower() == "true",
        )


@dataclass
class DatabaseConfig:
    """Database configuration."""

    host: str
    port: int
    database: str
    username: str
    password: str

    @property
    def connection_string(self) -> str:
        """Get the database connection string."""
        return f"postgresql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}"

    @classmethod
    def from_env(cls, prefix: str = "DB") -> "DatabaseConfig":
        """
        Load database configuration from environment variables.

        Args:
            prefix: Environment variable prefix (default: "DB")
        """
        return cls(
            host=os.environ.get(f"{prefix}_HOST", "localhost"),
            port=int(os.environ.get(f"{prefix}_PORT", "5432")),
            database=os.environ.get(f"{prefix}_NAME", "agentcore"),
            username=os.environ.get(f"{prefix}_USER", "postgres"),
            password=os.environ.get(f"{prefix}_PASSWORD", ""),
        )


@dataclass
class AppConfig:
    """Complete application configuration."""

    auth0: Auth0Config
    agent: AgentConfig
    database: Optional[DatabaseConfig] = None

    @classmethod
    def load(cls, agent_name: str, include_db: bool = False) -> "AppConfig":
        """
        Load complete application configuration.

        Args:
            agent_name: Name of the agent service
            include_db: Whether to include database configuration
        """
        return cls(
            auth0=Auth0Config.from_env(),
            agent=AgentConfig.from_env(agent_name),
            database=DatabaseConfig.from_env() if include_db else None,
        )


# Convenience function to load settings
def load_settings(agent_name: str, include_db: bool = False) -> AppConfig:
    """
    Load application settings from environment.

    Args:
        agent_name: Name of the agent service
        include_db: Whether to include database configuration

    Returns:
        AppConfig instance with all settings
    """
    return AppConfig.load(agent_name, include_db)


from .secrets_provider import (
    Auth0Secrets,
    AWSSecretsManagerProvider,
    CachedSecret,
    EnvironmentSecretsProvider,
    SecretAccessDeniedError,
    SecretNotFoundError,
    SecretsProvider,
    SecretsProviderError,
    get_auth0_secrets,
    get_default_provider,
    get_secrets_provider,
    is_aws_environment,
    reset_default_provider,
    set_default_provider,
)

__all__ = [
    # Configuration classes
    "Auth0Config",
    "AgentConfig",
    "DatabaseConfig",
    "AppConfig",
    "load_settings",
    # Secrets provider classes
    "SecretsProvider",
    "EnvironmentSecretsProvider",
    "AWSSecretsManagerProvider",
    "Auth0Secrets",
    "CachedSecret",
    # Secrets provider functions
    "get_secrets_provider",
    "get_default_provider",
    "set_default_provider",
    "reset_default_provider",
    "get_auth0_secrets",
    "is_aws_environment",
    # Secrets provider exceptions
    "SecretsProviderError",
    "SecretNotFoundError",
    "SecretAccessDeniedError",
]
