# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for the secrets provider module.

Tests cover:
- EnvironmentSecretsProvider
- AWSSecretsManagerProvider
- Auth0Secrets dataclass
- Factory functions
- Thread safety
- Cache behavior
"""

import json
import threading
import time
from unittest.mock import Mock

import pytest

from shared.config.secrets_provider import (
    Auth0Secrets,
    AWSSecretsManagerProvider,
    CachedSecret,
    EnvironmentSecretsProvider,
    SecretAccessDeniedError,
    SecretNotFoundError,
    SecretsProviderError,
    get_auth0_secrets,
    get_default_provider,
    get_secrets_provider,
    is_aws_environment,
    reset_default_provider,
    set_default_provider,
)

# =============================================================================
# Test Auth0Secrets Dataclass
# =============================================================================


class TestAuth0Secrets:
    """Tests for the Auth0Secrets dataclass."""

    def test_from_dict_complete(self):
        """Test creating Auth0Secrets from a complete dictionary."""
        data = {
            "domain": "test.auth0.com",
            "client_id": "test-client-id",
            "client_secret": "test-secret",
            "audience": "https://api.example.com",
        }
        secrets = Auth0Secrets.from_dict(data)

        assert secrets.domain == "test.auth0.com"
        assert secrets.client_id == "test-client-id"
        assert secrets.client_secret == "test-secret"
        assert secrets.audience == "https://api.example.com"

    def test_from_dict_with_defaults(self):
        """Test creating Auth0Secrets with default audience."""
        data = {
            "domain": "test.auth0.com",
            "client_id": "test-client-id",
            "client_secret": "test-secret",
        }
        secrets = Auth0Secrets.from_dict(data)

        assert secrets.audience == "https://agentcore-financial-api"

    def test_from_dict_empty(self):
        """Test creating Auth0Secrets from empty dictionary."""
        secrets = Auth0Secrets.from_dict({})

        assert secrets.domain == ""
        assert secrets.client_id == ""
        assert secrets.client_secret == ""
        assert secrets.audience == "https://agentcore-financial-api"

    def test_validate_success(self):
        """Test validation passes with all required fields."""
        secrets = Auth0Secrets(
            domain="test.auth0.com",
            client_id="test-client-id",
            client_secret="test-secret",
        )
        errors = secrets.validate()
        assert errors == []

    def test_validate_missing_domain(self):
        """Test validation fails with missing domain."""
        secrets = Auth0Secrets(
            domain="",
            client_id="test-client-id",
            client_secret="test-secret",
        )
        errors = secrets.validate()
        assert "Auth0 domain is required" in errors

    def test_validate_missing_all(self):
        """Test validation fails with all fields missing."""
        secrets = Auth0Secrets(domain="", client_id="", client_secret="")
        errors = secrets.validate()

        assert len(errors) == 3
        assert "Auth0 domain is required" in errors
        assert "Auth0 client_id is required" in errors
        assert "Auth0 client_secret is required" in errors


# =============================================================================
# Test CachedSecret
# =============================================================================


class TestCachedSecret:
    """Tests for the CachedSecret dataclass."""

    def test_not_expired(self):
        """Test cache entry is not expired within TTL."""
        cached = CachedSecret(
            value={"key": "value"},
            cached_at=time.time(),
        )
        assert not cached.is_expired(ttl_seconds=3600)

    def test_expired(self):
        """Test cache entry is expired after TTL."""
        cached = CachedSecret(
            value={"key": "value"},
            cached_at=time.time() - 3700,  # 3700 seconds ago
        )
        assert cached.is_expired(ttl_seconds=3600)

    def test_just_expired(self):
        """Test cache entry expires exactly at TTL boundary."""
        cached = CachedSecret(
            value={"key": "value"},
            cached_at=time.time() - 3601,  # Just over TTL
        )
        assert cached.is_expired(ttl_seconds=3600)


# =============================================================================
# Test EnvironmentSecretsProvider
# =============================================================================


class TestEnvironmentSecretsProvider:
    """Tests for the EnvironmentSecretsProvider."""

    def test_get_auth0_secrets(self, monkeypatch):
        """Test retrieving Auth0 secrets from environment variables."""
        monkeypatch.setenv("AUTH0_DOMAIN", "test.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.example.com")

        provider = EnvironmentSecretsProvider()
        secrets = provider.get_secret("agentcore/auth0")

        assert secrets["domain"] == "test.auth0.com"
        assert secrets["client_id"] == "test-client-id"
        assert secrets["client_secret"] == "test-secret"
        assert secrets["audience"] == "https://api.example.com"

    def test_get_auth0_secrets_with_prefix(self, monkeypatch):
        """Test retrieving Auth0 secrets with custom prefix."""
        monkeypatch.setenv("TEST_AUTH0_DOMAIN", "prefixed.auth0.com")
        monkeypatch.setenv("TEST_AUTH0_CLIENT_ID", "prefixed-client-id")
        monkeypatch.setenv("TEST_AUTH0_CLIENT_SECRET", "prefixed-secret")
        monkeypatch.setenv("TEST_AUTH0_AUDIENCE", "https://prefixed-api.example.com")

        provider = EnvironmentSecretsProvider(env_prefix="TEST")
        secrets = provider.get_secret("auth0")

        assert secrets["domain"] == "prefixed.auth0.com"
        assert secrets["client_id"] == "prefixed-client-id"

    def test_get_auth0_secrets_defaults(self, monkeypatch):
        """Test Auth0 secrets with default values."""
        # Clear any existing env vars
        for key in [
            "AUTH0_DOMAIN",
            "AUTH0_CLIENT_ID",
            "AUTH0_CLIENT_SECRET",
            "AUTH0_AUDIENCE",
        ]:
            monkeypatch.delenv(key, raising=False)

        provider = EnvironmentSecretsProvider()
        secrets = provider.get_secret("agentcore/auth0")

        assert secrets["domain"] == ""
        assert secrets["audience"] == "https://agentcore-financial-api"

    def test_get_generic_secret_json(self, monkeypatch):
        """Test retrieving a generic JSON secret from environment."""
        monkeypatch.setenv("MY_SECRET", json.dumps({"key": "value", "number": 42}))

        provider = EnvironmentSecretsProvider()
        secrets = provider.get_secret("my-secret")

        assert secrets["key"] == "value"
        assert secrets["number"] == 42

    def test_get_generic_secret_string(self, monkeypatch):
        """Test retrieving a plain string secret from environment."""
        monkeypatch.setenv("MY_SECRET", "plain-value")

        provider = EnvironmentSecretsProvider()
        secrets = provider.get_secret("my-secret")

        assert secrets["value"] == "plain-value"

    def test_get_secret_not_found(self, monkeypatch):
        """Test SecretNotFoundError when env var doesn't exist."""
        monkeypatch.delenv("NONEXISTENT_SECRET", raising=False)

        provider = EnvironmentSecretsProvider()
        with pytest.raises(SecretNotFoundError):
            provider.get_secret("nonexistent-secret")

    def test_refresh_secret(self, monkeypatch):
        """Test refresh_secret re-reads from environment."""
        monkeypatch.setenv("AUTH0_DOMAIN", "original.auth0.com")
        provider = EnvironmentSecretsProvider()

        secrets1 = provider.get_secret("auth0")
        assert secrets1["domain"] == "original.auth0.com"

        # Change env var
        monkeypatch.setenv("AUTH0_DOMAIN", "updated.auth0.com")
        secrets2 = provider.refresh_secret("auth0")
        assert secrets2["domain"] == "updated.auth0.com"

    def test_clear_cache_no_op(self):
        """Test clear_cache is a no-op for env provider."""
        provider = EnvironmentSecretsProvider()
        provider.clear_cache()  # Should not raise


# =============================================================================
# Test AWSSecretsManagerProvider
# =============================================================================


class TestAWSSecretsManagerProvider:
    """Tests for the AWSSecretsManagerProvider."""

    @pytest.fixture
    def mock_boto3_client(self):
        """Create a mock boto3 Secrets Manager client."""
        client = Mock()

        # Set up exceptions
        client.exceptions = Mock()
        client.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
        client.exceptions.AccessDeniedException = type("AccessDeniedException", (Exception,), {})

        return client

    def test_get_secret_success(self, mock_boto3_client):
        """Test successfully retrieving a secret."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps(
                {
                    "domain": "test.auth0.com",
                    "client_id": "test-client-id",
                    "client_secret": "test-secret",
                }
            ),
            "VersionId": "version-123",
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client)
        secrets = provider.get_secret("agentcore/auth0")

        assert secrets["domain"] == "test.auth0.com"
        assert secrets["client_id"] == "test-client-id"
        mock_boto3_client.get_secret_value.assert_called_once_with(
            SecretId="agentcore/auth0",
            VersionStage="AWSCURRENT",
        )

    def test_get_secret_with_version_stage(self, mock_boto3_client):
        """Test retrieving a secret with specific version stage."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "pending-value"}),
            "VersionId": "pending-version",
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client)
        provider.get_secret("my-secret", version_stage="AWSPENDING")

        mock_boto3_client.get_secret_value.assert_called_once_with(
            SecretId="my-secret",
            VersionStage="AWSPENDING",
        )

    def test_cache_hit(self, mock_boto3_client):
        """Test that cached secrets are returned without AWS call."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "value"}),
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=3600)

        # First call - should hit AWS
        secrets1 = provider.get_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 1

        # Second call - should use cache
        secrets2 = provider.get_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 1  # No additional call

        assert secrets1 == secrets2

    def test_cache_expiry(self, mock_boto3_client):
        """Test that expired cache entries are refreshed."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "value"}),
        }

        # Very short TTL for testing
        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=0)

        # First call
        provider.get_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 1

        # Second call - cache should be expired
        time.sleep(0.01)
        provider.get_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 2

    def test_refresh_secret_bypasses_cache(self, mock_boto3_client):
        """Test refresh_secret bypasses the cache."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "value"}),
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=3600)

        # Populate cache
        provider.get_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 1

        # Refresh should call AWS again
        provider.refresh_secret("my-secret")
        assert mock_boto3_client.get_secret_value.call_count == 2

    def test_clear_cache(self, mock_boto3_client):
        """Test clear_cache removes all cached entries."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "value"}),
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=3600)

        # Populate cache
        provider.get_secret("secret1")
        provider.get_secret("secret2")

        cache_info = provider.get_cache_info()
        assert cache_info["entries"] == 2

        # Clear cache
        provider.clear_cache()

        cache_info = provider.get_cache_info()
        assert cache_info["entries"] == 0

    def test_secret_not_found(self, mock_boto3_client):
        """Test SecretNotFoundError when secret doesn't exist."""
        mock_boto3_client.get_secret_value.side_effect = mock_boto3_client.exceptions.ResourceNotFoundException(
            "Not found"
        )

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, fallback_to_env=False)

        with pytest.raises(SecretNotFoundError):
            provider.get_secret("nonexistent-secret")

    def test_access_denied(self, mock_boto3_client):
        """Test SecretAccessDeniedError when access is denied."""
        mock_boto3_client.get_secret_value.side_effect = mock_boto3_client.exceptions.AccessDeniedException("Denied")

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, fallback_to_env=False)

        with pytest.raises(SecretAccessDeniedError):
            provider.get_secret("protected-secret")

    def test_fallback_to_env(self, mock_boto3_client, monkeypatch):
        """Test fallback to environment variables on SM failure."""
        mock_boto3_client.get_secret_value.side_effect = mock_boto3_client.exceptions.ResourceNotFoundException(
            "Not found"
        )

        monkeypatch.setenv("AUTH0_DOMAIN", "fallback.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "fallback-client")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "fallback-secret")

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, fallback_to_env=True)
        secrets = provider.get_secret("agentcore/auth0")

        assert secrets["domain"] == "fallback.auth0.com"

    def test_fallback_disabled(self, mock_boto3_client, monkeypatch):
        """Test that fallback can be disabled."""
        mock_boto3_client.get_secret_value.side_effect = mock_boto3_client.exceptions.ResourceNotFoundException(
            "Not found"
        )

        monkeypatch.setenv("AUTH0_DOMAIN", "fallback.auth0.com")

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, fallback_to_env=False)

        with pytest.raises(SecretNotFoundError):
            provider.get_secret("agentcore/auth0")

    def test_binary_secret(self, mock_boto3_client):
        """Test handling of binary secrets."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretBinary": b"binary-data",
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client)
        secrets = provider.get_secret("binary-secret")

        assert "binary" in secrets
        assert secrets["binary"] == b"binary-data"

    def test_plain_string_secret(self, mock_boto3_client):
        """Test handling of plain string (non-JSON) secrets."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": "plain-secret-value",
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client)
        secrets = provider.get_secret("plain-secret")

        assert secrets["value"] == "plain-secret-value"

    def test_thread_safety(self, mock_boto3_client):
        """Test thread-safe access to the cache."""
        call_count = {"value": 0}
        lock = threading.Lock()

        def mock_get_secret_value(**kwargs):
            with lock:
                call_count["value"] += 1
            time.sleep(0.01)  # Simulate network latency
            return {"SecretString": json.dumps({"key": "value"})}

        mock_boto3_client.get_secret_value.side_effect = mock_get_secret_value

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=3600)

        # Launch multiple threads simultaneously
        threads = []
        results = []
        results_lock = threading.Lock()

        def get_secret():
            result = provider.get_secret("my-secret")
            with results_lock:
                results.append(result)

        for _ in range(10):
            t = threading.Thread(target=get_secret)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should get the same result
        assert len(results) == 10
        assert all(r == results[0] for r in results)

        # Cache should prevent excessive AWS calls
        # Due to threading race conditions, we allow some initial calls before cache kicks in
        # The important thing is that we don't make 10 calls (one per thread)
        assert call_count["value"] < 10

    def test_get_cache_info(self, mock_boto3_client):
        """Test get_cache_info returns correct information."""
        mock_boto3_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"key": "value"}),
        }

        provider = AWSSecretsManagerProvider(client=mock_boto3_client, cache_ttl_seconds=3600)

        # Initially empty
        info = provider.get_cache_info()
        assert info["entries"] == 0
        assert info["ttl_seconds"] == 3600

        # After fetching
        provider.get_secret("secret1")
        info = provider.get_cache_info()
        assert info["entries"] == 1
        assert "secret1:AWSCURRENT" in info["secrets"]


# =============================================================================
# Test Factory Functions
# =============================================================================


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_is_aws_environment_lambda(self, monkeypatch):
        """Test AWS Lambda environment detection."""
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "my-function")
        assert is_aws_environment() is True

    def test_is_aws_environment_ecs(self, monkeypatch):
        """Test ECS environment detection."""
        monkeypatch.setenv("ECS_CONTAINER_METADATA_URI", "http://169.254.170.2/v4/...")
        assert is_aws_environment() is True

    def test_is_aws_environment_execution_env(self, monkeypatch):
        """Test AWS_EXECUTION_ENV detection."""
        monkeypatch.setenv("AWS_EXECUTION_ENV", "AWS_Lambda_python3.11")
        assert is_aws_environment() is True

    def test_is_aws_environment_local(self, monkeypatch):
        """Test local environment (no AWS markers)."""
        for key in [
            "AWS_LAMBDA_FUNCTION_NAME",
            "ECS_CONTAINER_METADATA_URI",
            "AWS_EXECUTION_ENV",
        ]:
            monkeypatch.delenv(key, raising=False)
        assert is_aws_environment() is False

    def test_get_secrets_provider_explicit_true(self, monkeypatch):
        """Test explicit USE_SECRETS_MANAGER=true."""
        provider = get_secrets_provider(use_secrets_manager=True)
        assert isinstance(provider, AWSSecretsManagerProvider)

    def test_get_secrets_provider_explicit_false(self, monkeypatch):
        """Test explicit USE_SECRETS_MANAGER=false."""
        provider = get_secrets_provider(use_secrets_manager=False)
        assert isinstance(provider, EnvironmentSecretsProvider)

    def test_get_secrets_provider_env_true(self, monkeypatch):
        """Test USE_SECRETS_MANAGER env var set to true."""
        monkeypatch.setenv("USE_SECRETS_MANAGER", "true")
        provider = get_secrets_provider()
        assert isinstance(provider, AWSSecretsManagerProvider)

    def test_get_secrets_provider_env_false(self, monkeypatch):
        """Test USE_SECRETS_MANAGER env var set to false."""
        monkeypatch.setenv("USE_SECRETS_MANAGER", "false")
        provider = get_secrets_provider()
        assert isinstance(provider, EnvironmentSecretsProvider)

    def test_get_secrets_provider_auto_detect_aws(self, monkeypatch):
        """Test auto-detection in AWS environment."""
        monkeypatch.delenv("USE_SECRETS_MANAGER", raising=False)
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "my-function")
        provider = get_secrets_provider()
        assert isinstance(provider, AWSSecretsManagerProvider)

    def test_get_secrets_provider_auto_detect_local(self, monkeypatch):
        """Test auto-detection in local environment."""
        for key in [
            "USE_SECRETS_MANAGER",
            "AWS_LAMBDA_FUNCTION_NAME",
            "ECS_CONTAINER_METADATA_URI",
            "AWS_EXECUTION_ENV",
        ]:
            monkeypatch.delenv(key, raising=False)
        provider = get_secrets_provider()
        assert isinstance(provider, EnvironmentSecretsProvider)


class TestDefaultProvider:
    """Tests for default provider management."""

    def setup_method(self):
        """Reset default provider before each test."""
        reset_default_provider()

    def teardown_method(self):
        """Reset default provider after each test."""
        reset_default_provider()

    def test_get_default_provider_singleton(self, monkeypatch):
        """Test that get_default_provider returns a singleton."""
        monkeypatch.setenv("USE_SECRETS_MANAGER", "false")

        provider1 = get_default_provider()
        provider2 = get_default_provider()

        assert provider1 is provider2

    def test_set_default_provider(self, monkeypatch):
        """Test setting a custom default provider."""
        custom_provider = EnvironmentSecretsProvider()
        set_default_provider(custom_provider)

        provider = get_default_provider()
        assert provider is custom_provider

    def test_reset_default_provider(self, monkeypatch):
        """Test resetting the default provider."""
        monkeypatch.setenv("USE_SECRETS_MANAGER", "false")

        provider1 = get_default_provider()
        reset_default_provider()
        provider2 = get_default_provider()

        assert provider1 is not provider2


class TestGetAuth0Secrets:
    """Tests for the get_auth0_secrets convenience function."""

    def setup_method(self):
        """Reset default provider before each test."""
        reset_default_provider()

    def teardown_method(self):
        """Reset default provider after each test."""
        reset_default_provider()

    def test_get_auth0_secrets_from_env(self, monkeypatch):
        """Test getting Auth0 secrets from environment."""
        monkeypatch.setenv("USE_SECRETS_MANAGER", "false")
        monkeypatch.setenv("AUTH0_DOMAIN", "test.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test-secret")
        monkeypatch.setenv("AUTH0_AUDIENCE", "https://api.example.com")

        secrets = get_auth0_secrets()

        assert isinstance(secrets, Auth0Secrets)
        assert secrets.domain == "test.auth0.com"
        assert secrets.client_id == "test-client-id"
        assert secrets.client_secret == "test-secret"
        assert secrets.audience == "https://api.example.com"

    def test_get_auth0_secrets_custom_provider(self, monkeypatch):
        """Test getting Auth0 secrets with custom provider."""
        monkeypatch.setenv("AUTH0_DOMAIN", "custom.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "custom-client")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "custom-secret")

        custom_provider = EnvironmentSecretsProvider()
        secrets = get_auth0_secrets(provider=custom_provider)

        assert secrets.domain == "custom.auth0.com"

    def test_get_auth0_secrets_custom_name(self, monkeypatch):
        """Test getting Auth0 secrets with custom secret name."""
        monkeypatch.setenv("SECRET_NAME_AUTH0", "my-custom/auth0")
        monkeypatch.setenv("USE_SECRETS_MANAGER", "false")
        monkeypatch.setenv("AUTH0_DOMAIN", "named.auth0.com")
        monkeypatch.setenv("AUTH0_CLIENT_ID", "named-client")
        monkeypatch.setenv("AUTH0_CLIENT_SECRET", "named-secret")

        # The secret name is used, but env provider ignores it for auth0
        secrets = get_auth0_secrets()
        assert secrets.domain == "named.auth0.com"


# =============================================================================
# Test Exception Classes
# =============================================================================


class TestExceptions:
    """Tests for exception classes."""

    def test_secrets_provider_error(self):
        """Test SecretsProviderError is base exception."""
        error = SecretsProviderError("Test error")
        assert str(error) == "Test error"
        assert isinstance(error, Exception)

    def test_secret_not_found_error(self):
        """Test SecretNotFoundError inherits from base."""
        error = SecretNotFoundError("Secret not found")
        assert isinstance(error, SecretsProviderError)

    def test_secret_access_denied_error(self):
        """Test SecretAccessDeniedError inherits from base."""
        error = SecretAccessDeniedError("Access denied")
        assert isinstance(error, SecretsProviderError)
