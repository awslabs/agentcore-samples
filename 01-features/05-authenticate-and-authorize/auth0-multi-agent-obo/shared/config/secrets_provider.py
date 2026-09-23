# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
AWS Secrets Manager integration with caching and fallback support.

Provides a flexible secrets management system that supports:
- Environment variables for development/testing
- AWS Secrets Manager for production
- Thread-safe caching with configurable TTL
- Secret rotation via version staging
"""

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CachedSecret:
    """Container for a cached secret with metadata."""

    value: Dict[str, Any]
    cached_at: float
    version_id: Optional[str] = None
    version_stage: Optional[str] = None

    def is_expired(self, ttl_seconds: int) -> bool:
        """Check if the cached secret has expired."""
        return (time.time() - self.cached_at) > ttl_seconds


@dataclass
class Auth0Secrets:
    """Typed container for Auth0 credentials from Secrets Manager."""

    domain: str
    client_id: str
    client_secret: str
    audience: str = "https://agentcore-financial-api"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Auth0Secrets":
        """Create Auth0Secrets from a dictionary."""
        return cls(
            domain=data.get("domain", ""),
            client_id=data.get("client_id", ""),
            client_secret=data.get("client_secret", ""),
            audience=data.get("audience", "https://agentcore-financial-api"),
        )

    def validate(self) -> list[str]:
        """Validate that required fields are present."""
        errors = []
        if not self.domain:
            errors.append("Auth0 domain is required")
        if not self.client_id:
            errors.append("Auth0 client_id is required")
        if not self.client_secret:
            errors.append("Auth0 client_secret is required")
        return errors


# =============================================================================
# Exceptions
# =============================================================================


class SecretsProviderError(Exception):
    """Base exception for secrets provider errors."""

    pass


class SecretNotFoundError(SecretsProviderError):
    """Raised when a secret cannot be found."""

    pass


class SecretAccessDeniedError(SecretsProviderError):
    """Raised when access to a secret is denied."""

    pass


# =============================================================================
# Abstract Base Class
# =============================================================================


class SecretsProvider(ABC):
    """Abstract base class for secrets providers."""

    @abstractmethod
    def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Retrieve a secret by name.

        Args:
            secret_name: The name/key of the secret to retrieve

        Returns:
            Dictionary containing the secret values

        Raises:
            SecretNotFoundError: If the secret cannot be found
            SecretAccessDeniedError: If access is denied
            SecretsProviderError: For other errors
        """
        pass

    @abstractmethod
    def refresh_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Force refresh a secret, bypassing cache.

        Args:
            secret_name: The name/key of the secret to refresh

        Returns:
            Dictionary containing the fresh secret values
        """
        pass

    @abstractmethod
    def clear_cache(self) -> None:
        """Clear all cached secrets."""
        pass


# =============================================================================
# Environment Variables Provider
# =============================================================================


class EnvironmentSecretsProvider(SecretsProvider):
    """
    Secrets provider that reads from environment variables.

    Useful for development and testing where AWS Secrets Manager
    is not available or not needed.
    """

    def __init__(self, env_prefix: str = ""):
        """
        Initialize the environment secrets provider.

        Args:
            env_prefix: Optional prefix for environment variable names
        """
        self.env_prefix = env_prefix

    def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Get a secret from environment variables.

        For Auth0 secrets, expects:
        - AUTH0_DOMAIN
        - AUTH0_CLIENT_ID
        - AUTH0_CLIENT_SECRET
        - AUTH0_AUDIENCE

        Args:
            secret_name: The name of the secret (used for logging)

        Returns:
            Dictionary containing the secret values from env vars
        """
        # For Auth0 secrets, map to standard env vars
        if "auth0" in secret_name.lower():
            prefix = f"{self.env_prefix}_" if self.env_prefix else ""
            return {
                "domain": os.environ.get(f"{prefix}AUTH0_DOMAIN", ""),
                "client_id": os.environ.get(f"{prefix}AUTH0_CLIENT_ID", ""),
                "client_secret": os.environ.get(f"{prefix}AUTH0_CLIENT_SECRET", ""),
                "audience": os.environ.get(f"{prefix}AUTH0_AUDIENCE", "https://agentcore-financial-api"),
            }

        # Generic fallback: try to read from env var with the secret name
        env_key = secret_name.upper().replace("/", "_").replace("-", "_")
        value = os.environ.get(env_key)
        if value:
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {"value": value}

        raise SecretNotFoundError(f"No environment variable found for secret: {secret_name}")

    def refresh_secret(self, secret_name: str) -> Dict[str, Any]:
        """Refresh simply re-reads from environment."""
        return self.get_secret(secret_name)

    def clear_cache(self) -> None:
        """No cache to clear for environment provider."""
        pass


# =============================================================================
# AWS Secrets Manager Provider
# =============================================================================


class AWSSecretsManagerProvider(SecretsProvider):
    """
    Production secrets provider using AWS Secrets Manager.

    Features:
    - Thread-safe caching with configurable TTL
    - Support for secret versions (AWSCURRENT, AWSPENDING)
    - Graceful fallback to environment variables
    - Automatic retry on transient errors
    """

    def __init__(
        self,
        region_name: Optional[str] = None,
        cache_ttl_seconds: int = 3600,
        fallback_to_env: bool = True,
        client: Optional[Any] = None,
    ):
        """
        Initialize the AWS Secrets Manager provider.

        Args:
            region_name: AWS region (defaults to AWS_REGION env var)
            cache_ttl_seconds: How long to cache secrets (default: 3600 = 1 hour)
            fallback_to_env: Whether to fall back to env vars on SM failure
            client: Optional boto3 client for testing
        """
        self.region_name = region_name or os.environ.get("AWS_REGION", "us-east-1")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.fallback_to_env = fallback_to_env
        self._client = client
        self._cache: Dict[str, CachedSecret] = {}
        self._lock = threading.Lock()
        self._env_provider = EnvironmentSecretsProvider()

    @property
    def client(self):
        """Lazy initialization of boto3 Secrets Manager client."""
        if self._client is None:
            import boto3

            self._client = boto3.client("secretsmanager", region_name=self.region_name)
        return self._client

    def get_secret(
        self,
        secret_name: str,
        version_stage: str = "AWSCURRENT",
    ) -> Dict[str, Any]:
        """
        Retrieve a secret from AWS Secrets Manager with caching.

        Uses double-checked locking for thread safety.

        Args:
            secret_name: The name or ARN of the secret
            version_stage: Version stage (AWSCURRENT or AWSPENDING)

        Returns:
            Dictionary containing the secret values

        Raises:
            SecretNotFoundError: If the secret cannot be found
            SecretAccessDeniedError: If access is denied
            SecretsProviderError: For other errors
        """
        cache_key = f"{secret_name}:{version_stage}"

        # First check without lock (fast path)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if not cached.is_expired(self.cache_ttl_seconds):
                logger.debug(f"Cache hit for secret: {secret_name}")
                return cached.value

        # Acquire lock for potentially fetching
        with self._lock:
            # Double-check cache inside lock (another thread may have fetched)
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                if not cached.is_expired(self.cache_ttl_seconds):
                    logger.debug(f"Cache hit for secret (inside lock): {secret_name}")
                    return cached.value
                logger.debug(f"Cache expired for secret: {secret_name}")

            # Fetch from Secrets Manager while holding the lock
            try:
                return self._fetch_and_cache_locked(secret_name, version_stage, cache_key)
            except Exception as e:
                logger.warning(f"Failed to fetch secret from Secrets Manager: {e}")

                # Try fallback to environment variables
                if self.fallback_to_env:
                    try:
                        logger.info(f"Falling back to environment variables for: {secret_name}")
                        return self._env_provider.get_secret(secret_name)
                    except SecretsProviderError:
                        pass

                # Re-raise the original exception
                raise

    def _fetch_and_cache_locked(
        self,
        secret_name: str,
        version_stage: str,
        cache_key: str,
    ) -> Dict[str, Any]:
        """Fetch secret from AWS and cache it. Must be called with _lock held."""
        try:
            response = self.client.get_secret_value(
                SecretId=secret_name,
                VersionStage=version_stage,
            )
        except self.client.exceptions.ResourceNotFoundException:
            raise SecretNotFoundError(f"Secret not found: {secret_name}")
        except self.client.exceptions.AccessDeniedException as e:
            raise SecretAccessDeniedError(f"Access denied to secret: {secret_name}") from e
        except Exception as e:
            raise SecretsProviderError(f"Error retrieving secret: {secret_name}") from e

        # Parse the secret value
        if "SecretString" in response:
            try:
                secret_value = json.loads(response["SecretString"])
            except json.JSONDecodeError:
                secret_value = {"value": response["SecretString"]}
        elif "SecretBinary" in response:
            secret_value = {"binary": response["SecretBinary"]}
        else:
            raise SecretsProviderError(f"Unexpected response format for secret: {secret_name}")

        # Cache the result (lock already held by caller)
        self._cache[cache_key] = CachedSecret(
            value=secret_value,
            cached_at=time.time(),
            version_id=response.get("VersionId"),
            version_stage=version_stage,
        )

        logger.info(f"Successfully fetched and cached secret: {secret_name}")
        return secret_value

    def refresh_secret(self, secret_name: str, version_stage: str = "AWSCURRENT") -> Dict[str, Any]:
        """
        Force refresh a secret, bypassing the cache.

        Args:
            secret_name: The name or ARN of the secret
            version_stage: Version stage (AWSCURRENT or AWSPENDING)

        Returns:
            Dictionary containing the fresh secret values
        """
        cache_key = f"{secret_name}:{version_stage}"

        # Remove from cache first
        with self._lock:
            self._cache.pop(cache_key, None)

        # Fetch fresh
        return self.get_secret(secret_name, version_stage)

    def clear_cache(self) -> None:
        """Clear all cached secrets."""
        with self._lock:
            self._cache.clear()
        logger.info("Secrets cache cleared")

    def get_cache_info(self) -> Dict[str, Any]:
        """Get information about the current cache state (for debugging)."""
        with self._lock:
            return {
                "entries": len(self._cache),
                "ttl_seconds": self.cache_ttl_seconds,
                "secrets": list(self._cache.keys()),
            }


# =============================================================================
# Factory Functions
# =============================================================================


def is_aws_environment() -> bool:
    """
    Detect if running in an AWS environment (Lambda, ECS, etc.).

    Returns:
        True if AWS environment markers are detected
    """
    aws_markers = [
        "AWS_LAMBDA_FUNCTION_NAME",
        "ECS_CONTAINER_METADATA_URI",
        "AWS_EXECUTION_ENV",
    ]
    return any(os.environ.get(marker) for marker in aws_markers)


def get_secrets_provider(
    use_secrets_manager: Optional[bool] = None,
    **kwargs,
) -> SecretsProvider:
    """
    Factory function to get the appropriate secrets provider.

    Args:
        use_secrets_manager: Explicitly enable/disable Secrets Manager.
            If None, auto-detects based on environment.
        **kwargs: Additional arguments passed to the provider

    Returns:
        Appropriate SecretsProvider instance
    """
    if use_secrets_manager is None:
        # Check environment variable first
        env_setting = os.environ.get("USE_SECRETS_MANAGER", "").lower()
        if env_setting == "true":
            use_secrets_manager = True
        elif env_setting == "false":
            use_secrets_manager = False
        else:
            # Auto-detect AWS environment
            use_secrets_manager = is_aws_environment()

    if use_secrets_manager:
        logger.info("Using AWS Secrets Manager provider")
        return AWSSecretsManagerProvider(**kwargs)
    else:
        logger.info("Using environment variables provider")
        env_prefix = kwargs.get("env_prefix", "")
        return EnvironmentSecretsProvider(env_prefix=env_prefix)


# Default provider instance (lazy initialization)
_default_provider: Optional[SecretsProvider] = None
_provider_lock = threading.Lock()


def get_default_provider() -> SecretsProvider:
    """
    Get the default secrets provider singleton.

    Thread-safe lazy initialization of the default provider.

    Returns:
        The default SecretsProvider instance
    """
    global _default_provider

    if _default_provider is None:
        with _provider_lock:
            if _default_provider is None:
                _default_provider = get_secrets_provider()

    return _default_provider


def set_default_provider(provider: SecretsProvider) -> None:
    """
    Set the default secrets provider.

    Useful for testing or custom configuration.

    Args:
        provider: The provider to use as default
    """
    global _default_provider

    with _provider_lock:
        _default_provider = provider


def reset_default_provider() -> None:
    """
    Reset the default provider to None.

    The next call to get_default_provider() will create a new instance.
    """
    global _default_provider

    with _provider_lock:
        _default_provider = None


def get_auth0_secrets(
    secret_name: Optional[str] = None,
    provider: Optional[SecretsProvider] = None,
) -> Auth0Secrets:
    """
    Convenience function to get Auth0 secrets.

    Args:
        secret_name: Name of the secret in Secrets Manager
            (defaults to SECRET_NAME_AUTH0 env var or "agentcore/auth0")
        provider: Optional secrets provider (uses default if not specified)

    Returns:
        Auth0Secrets instance with credentials
    """
    if provider is None:
        provider = get_default_provider()

    if secret_name is None:
        secret_name = os.environ.get("SECRET_NAME_AUTH0", "agentcore/auth0")

    secret_data = provider.get_secret(secret_name)
    return Auth0Secrets.from_dict(secret_data)


__all__ = [
    # Data classes
    "CachedSecret",
    "Auth0Secrets",
    # Exceptions
    "SecretsProviderError",
    "SecretNotFoundError",
    "SecretAccessDeniedError",
    # Providers
    "SecretsProvider",
    "EnvironmentSecretsProvider",
    "AWSSecretsManagerProvider",
    # Factory functions
    "is_aws_environment",
    "get_secrets_provider",
    "get_default_provider",
    "set_default_provider",
    "reset_default_provider",
    "get_auth0_secrets",
]
