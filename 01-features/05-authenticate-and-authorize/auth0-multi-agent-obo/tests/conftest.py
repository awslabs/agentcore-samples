# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Pytest configuration and shared fixtures for AgentCore Identity tests.

This module provides reusable fixtures for testing authentication,
authorization, and agent interactions.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Dict
from unittest.mock import Mock

import jwt
import pytest

# Sample secret for HS256 JWT signing (for testing)
SAMPLE_JWT_SECRET = "test-secret-key-for-jwt-signing-in-tests-only"

# Public key placeholder for tests that need it
SAMPLE_PUBLIC_KEY = SAMPLE_JWT_SECRET  # Use same secret for HS256 symmetric verification


@pytest.fixture
def sample_jwt_payload() -> Dict[str, Any]:
    """Sample JWT payload with custom claims.

    Contains both `scope` (all API scopes, client-level) and `permissions`
    (RBAC-restricted, user-level). The coordinator reads `permissions` as the
    primary authorization source, falling back to `scope` for non-Auth0 IdPs.
    """
    now = datetime.utcnow()
    return {
        "iss": "https://your-tenant.auth0.com/",
        "sub": "auth0|123456789",
        "aud": [
            "https://agentcore-financial-api",
            "https://your-tenant.auth0.com/userinfo",
        ],
        "azp": "abc123ClientId",
        "exp": int((now + timedelta(hours=24)).timestamp()),
        "iat": int(now.timestamp()),
        # scope: ALL API scopes (client-level, not RBAC-restricted)
        "scope": (
            "openid profile email "
            "profile:personal:read profile:personal:write "
            "profile:preferences:read profile:preferences:write "
            "accounts:savings:read accounts:savings:write "
            "accounts:transaction:read "
            "accounts:credit:read accounts:credit:write "
            "accounts:investment:read"
        ),
        # permissions: RBAC-restricted (user-level, per Auth0 role assignment)
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
        # Custom claims
        "https://agentcore.example.com/customer_id": "CUST-12345",
        "https://agentcore.example.com/account_types": ["savings", "checking"],
        "https://agentcore.example.com/account_ids": ["ACC-001", "ACC-002"],
        "https://agentcore.example.com/roles": ["customer", "premium"],
        "https://agentcore.example.com/kyc_status": "verified",
        # Standard claims
        "email": "john.doe@example.com",
        "email_verified": True,
        "name": "John Doe",
        "nickname": "johndoe",
        "picture": "https://example.com/avatar.jpg",
    }


@pytest.fixture
def profile_only_jwt_claims() -> Dict[str, Any]:
    """JWT claims where scope has all scopes but permissions has only profile scopes.

    This simulates an Auth0 user assigned to a profile-only role. The `scope`
    claim still contains all API scopes (client-level), but `permissions` is
    restricted to profile scopes only. The coordinator must use `permissions`
    (RBAC) and ignore the broader `scope` claim.
    """
    now = datetime.utcnow()
    return {
        "iss": "https://your-tenant.auth0.com/",
        "sub": "auth0|profile-only-user",
        "aud": [
            "https://agentcore-financial-api",
            "https://your-tenant.auth0.com/userinfo",
        ],
        "azp": "abc123ClientId",
        "exp": int((now + timedelta(hours=24)).timestamp()),
        "iat": int(now.timestamp()),
        # scope: ALL API scopes (client-level — same for every user)
        "scope": (
            "openid profile email "
            "profile:personal:read profile:personal:write "
            "profile:preferences:read profile:preferences:write "
            "accounts:savings:read accounts:savings:write "
            "accounts:transaction:read "
            "accounts:credit:read accounts:credit:write "
            "accounts:investment:read"
        ),
        # permissions: RBAC-restricted — profile-only role, NO accounts scopes
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        ],
        # Custom claims
        "https://agentcore.example.com/customer_id": "CUST-00099",
        "https://agentcore.example.com/account_types": [],
        "https://agentcore.example.com/account_ids": [],
        "https://agentcore.example.com/roles": ["customer"],
        "https://agentcore.example.com/kyc_status": "verified",
        # Standard claims
        "email": "profile-only@example.com",
        "email_verified": True,
        "name": "Profile Only User",
        "nickname": "profileonly",
        "picture": "https://example.com/avatar.jpg",
    }


@pytest.fixture
def sample_jwt_token(sample_jwt_payload: Dict[str, Any]) -> str:
    """Generate a sample JWT token for testing."""
    return jwt.encode(sample_jwt_payload, SAMPLE_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def expired_jwt_token() -> str:
    """Generate an expired JWT token for testing."""
    now = datetime.utcnow()
    payload = {
        "iss": "https://your-tenant.auth0.com/",
        "sub": "auth0|123456789",
        "aud": "https://agentcore-financial-api",
        "exp": int((now - timedelta(hours=1)).timestamp()),  # Expired 1 hour ago
        "iat": int((now - timedelta(hours=2)).timestamp()),
        "scope": "openid profile email",
    }
    return jwt.encode(payload, SAMPLE_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def invalid_audience_token() -> str:
    """Generate a JWT token with invalid audience."""
    now = datetime.utcnow()
    payload = {
        "iss": "https://your-tenant.auth0.com/",
        "sub": "auth0|123456789",
        "aud": "https://wrong-audience.com",
        "exp": int((now + timedelta(hours=24)).timestamp()),
        "iat": int(now.timestamp()),
        "scope": "openid profile email",
    }
    return jwt.encode(payload, SAMPLE_JWT_SECRET, algorithm="HS256")


@pytest.fixture
def sample_user_context() -> Dict[str, Any]:
    """Sample user context extracted from JWT.

    The `permissions` field reflects RBAC-restricted permissions (from the
    `permissions` claim). The `scopes` field is retained for backward
    compatibility with tests that reference it.
    """
    return {
        "user_id": "auth0|123456789",
        "email": "john.doe@example.com",
        "name": "John Doe",
        "customer_id": "CUST-12345",
        "account_types": ["savings", "checking"],
        "account_ids": ["ACC-001", "ACC-002"],
        "roles": ["customer", "premium"],
        "kyc_status": "verified",
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
        "scopes": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
    }


@pytest.fixture
def profile_only_user_context() -> Dict[str, Any]:
    """User context with only profile permissions (no accounts access).

    Simulates a user whose Auth0 RBAC role grants only profile permissions.
    """
    return {
        "user_id": "auth0|profile-only-user",
        "email": "profile-only@example.com",
        "name": "Profile Only User",
        "customer_id": "CUST-00099",
        "account_types": [],
        "account_ids": [],
        "roles": ["customer"],
        "kyc_status": "verified",
        "permissions": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        ],
        "scopes": [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        ],
    }


@pytest.fixture
def accounts_only_user_context() -> Dict[str, Any]:
    """User context with only accounts permissions (no profile access).

    Simulates a user whose Auth0 RBAC role grants only accounts permissions.
    """
    return {
        "user_id": "auth0|accounts-only-user",
        "email": "accounts-only@example.com",
        "name": "Accounts Only User",
        "customer_id": "CUST-00088",
        "account_types": ["savings"],
        "account_ids": ["ACC-099"],
        "roles": ["customer"],
        "kyc_status": "verified",
        "permissions": [
            "openid",
            "profile",
            "email",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
        "scopes": [
            "openid",
            "profile",
            "email",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        ],
    }


@pytest.fixture
def sample_customer_profile() -> Dict[str, Any]:
    """Sample customer profile data (simplified)."""
    return {
        "customer_id": "CUST-12345",
        "name": "John Doe",
        "email": "john.doe@example.com",
        "phone": "+61412345678",
        "last_updated": "2024-01-15T10:30:00Z",
    }


@pytest.fixture
def sample_account() -> Dict[str, Any]:
    """Sample account data."""
    return {
        "account_id": "ACC-001",
        "customer_id": "CUST-12345",
        "account_type": "savings",
        "account_number": "123456789",
        "bsb": "083-001",
        "balance": 15000.50,
        "currency": "AUD",
        "status": "active",
        "created_at": "2024-01-15T10:00:00Z",
    }


@pytest.fixture
def mock_agentcore_client() -> Mock:
    """Mock AgentCore Runtime client."""
    client = Mock()

    # Mock invoke_agent response
    client.invoke_agent.return_value = {
        "completion": "Agent response text",
        "memory_id": "mem-12345",
        "trace": {"agent_id": "agent-123", "session_id": "session-456"},
    }

    # Mock agent-to-agent communication
    client.invoke_collaborator.return_value = {
        "response": {"status": "success", "data": {}},
        "trace_id": "trace-789",
    }

    return client


@pytest.fixture
def mock_auth0_jwks() -> Dict[str, Any]:
    """Mock Auth0 JWKS (JSON Web Key Set) response."""
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": "test-key-id",
                "alg": "RS256",
                "n": "u1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0guntDKgZda5qeRvJGldjTMZ0o5EuZp-N8qFr0YVwmQ6Zpy5JJoNw8TyFJxz4BdqMJBP6k4s7aL1pPMhPbKYoVvPaVy2z2P8hqZBCJ3gW3l2L0u8nhYZkNIc1Lw3jqcLQ8cApeVpz0WqZsCqcLNgxzfvIvjrXfKNgQ3dKRLwwjg3sUmK6kBKCGmkxwlqYvH3oWXRiQYqB7JLHb8JcsFJ0h9VYQ3mLNZ9cBDhYd8RBxiNGLYHAOaNH6dxKKWEtdCh6EQJWoNQvSmfCjhPWmJWV3vKdCn6XZK3Tqgv2p6a6Q",
                "e": "AQAB",
            }
        ]
    }


@pytest.fixture
def mock_jwt_validator() -> Mock:
    """Mock JWT validator."""
    validator = Mock()
    validator.validate_token.return_value = {
        "valid": True,
        "payload": {
            "sub": "auth0|123456789",
            "email": "john.doe@example.com",
            "https://agentcore.example.com/customer_id": "CUST-12345",
        },
    }
    return validator


@pytest.fixture
def auth_headers(sample_jwt_token: str) -> Dict[str, str]:
    """Generate authentication headers with JWT token."""
    return {
        "Authorization": f"Bearer {sample_jwt_token}",
        "Content-Type": "application/json",
    }


@pytest.fixture
def mock_dynamodb_table() -> Mock:
    """Mock DynamoDB table for profile storage."""
    table = Mock()
    table.get_item.return_value = {
        "Item": {
            "customer_id": "CUST-12345",
            "user_id": "auth0|123456789",
            "email": "john.doe@example.com",
        }
    }
    table.put_item.return_value = {}
    table.update_item.return_value = {}
    table.query.return_value = {"Items": []}
    return table


@pytest.fixture
def mock_secrets_manager() -> Mock:
    """Mock AWS Secrets Manager client."""
    client = Mock()
    client.get_secret_value.return_value = {
        "SecretString": json.dumps(
            {
                "domain": "your-tenant.auth0.com",
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "audience": "https://agentcore-financial-api",
            }
        ),
        "VersionId": "version-123",
    }

    # Set up exceptions for the mock client
    client.exceptions = Mock()
    client.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
    client.exceptions.AccessDeniedException = type("AccessDeniedException", (Exception,), {})

    return client


@pytest.fixture
def mock_secrets_manager_with_versions() -> Mock:
    """Mock AWS Secrets Manager client with version staging support for rotation testing."""
    client = Mock()

    # Set up exceptions for the mock client
    client.exceptions = Mock()
    client.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
    client.exceptions.AccessDeniedException = type("AccessDeniedException", (Exception,), {})

    def get_secret_value(SecretId, VersionStage="AWSCURRENT"):
        if VersionStage == "AWSCURRENT":
            return {
                "SecretString": json.dumps(
                    {
                        "domain": "your-tenant.auth0.com",
                        "client_id": "current_client_id",
                        "client_secret": "current_client_secret",
                        "audience": "https://agentcore-financial-api",
                    }
                ),
                "VersionId": "current-version-123",
            }
        elif VersionStage == "AWSPENDING":
            return {
                "SecretString": json.dumps(
                    {
                        "domain": "your-tenant.auth0.com",
                        "client_id": "pending_client_id",
                        "client_secret": "pending_client_secret",
                        "audience": "https://agentcore-financial-api",
                    }
                ),
                "VersionId": "pending-version-456",
            }
        else:
            raise client.exceptions.ResourceNotFoundException(f"Unknown version stage: {VersionStage}")

    client.get_secret_value.side_effect = get_secret_value
    return client


@pytest.fixture
def mock_secrets_provider(monkeypatch) -> Mock:
    """
    Mock secrets provider for testing code that consumes secrets.

    This fixture also resets the default provider after the test.
    """
    from shared.config.secrets_provider import (
        EnvironmentSecretsProvider,
        reset_default_provider,
        set_default_provider,
    )

    # Set env vars for the provider
    monkeypatch.setenv("AUTH0_DOMAIN", "your-tenant.auth0.com")
    monkeypatch.setenv("AUTH0_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("AUTH0_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("AUTH0_AUDIENCE", "https://agentcore-financial-api")
    monkeypatch.setenv("USE_SECRETS_MANAGER", "false")

    # Create and set provider
    provider = EnvironmentSecretsProvider()
    set_default_provider(provider)

    yield provider

    # Reset after test
    reset_default_provider()


# Environment variable fixtures
@pytest.fixture
def mock_env_vars(monkeypatch):
    """Set up mock environment variables."""
    env_vars = {
        "AUTH0_DOMAIN": "your-tenant.auth0.com",
        "AUTH0_CLIENT_ID": "test_client_id",
        "AUTH0_CLIENT_SECRET": "test_client_secret",
        "AUTH0_AUDIENCE": "https://agentcore-financial-api",
        "AUTH0_CALLBACK_URL": "http://localhost:9090/callback",
        "AWS_REGION": "us-east-1",
        "COORDINATOR_AGENT_ID": "coord-agent-123",
        "PROFILE_AGENT_ID": "profile-agent-456",
        "ACCOUNTS_AGENT_ID": "accounts-agent-789",
        "AGENTCORE_IDENTITY_POOL_ID": "identity-pool-123",
        "AGENTCORE_JWT_AUTHORIZER_ID": "jwt-auth-456",
        "AGENTCORE_MEMORY_ID": "memory-789",
        "AGENTCORE_GATEWAY_URL": "https://agentcore.us-east-1.amazonaws.com",
        "DEBUG": "false",
        "LOG_LEVEL": "INFO",
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    return env_vars


@pytest.fixture
def sample_coordinator_request() -> Dict[str, Any]:
    """Sample request to coordinator agent."""
    return {
        "input": "Show me my account balances",
        "session_id": "session-123",
        "user_context": {"customer_id": "CUST-12345", "user_id": "auth0|123456789"},
    }


@pytest.fixture
def sample_profile_request() -> Dict[str, Any]:
    """Sample request to profile agent."""
    return {
        "action": "get_profile",
        "customer_id": "CUST-12345",
        "user_context": {
            "customer_id": "CUST-12345",
            "user_id": "auth0|123456789",
            "scopes": ["profile:personal:read"],
        },
    }


# HTTP client mocking fixtures for JWT forwarding tests


@pytest.fixture
def mock_http_client() -> Mock:
    """Mock AgentHttpClient for testing agent-to-agent HTTP calls."""
    from shared.http.agent_http_client import AgentHttpClient

    client = Mock(spec=AgentHttpClient)

    # Default successful response
    client.invoke_agent.return_value = {
        "response": '{"status": "success", "data": {}}',
        "session_id": "session-123",
        "metadata": {},
        "raw_response": {"status": "success", "data": {}},
    }

    return client


@pytest.fixture
def mock_http_response_factory():
    """Factory for creating mock HTTP responses."""

    def create_response(
        status_code: int = 200,
        json_data: Dict[str, Any] = None,
        text: str = None,
        headers: Dict[str, str] = None,
    ) -> Mock:
        """Create a mock requests.Response object."""
        response = Mock()
        response.status_code = status_code
        response.headers = headers or {}

        if json_data is not None:
            response.json.return_value = json_data
            response.text = json.dumps(json_data)
        elif text is not None:
            response.text = text
            response.json.side_effect = json.JSONDecodeError("", "", 0)
        else:
            response.json.return_value = {}
            response.text = "{}"

        return response

    return create_response


@pytest.fixture
def mock_successful_agent_response(mock_http_response_factory) -> Mock:
    """Mock successful HTTP response from action agent."""
    return mock_http_response_factory(
        status_code=200,
        json_data={
            "output": "Profile retrieved successfully",
            "metadata": {"agent": "profile"},
        },
    )


@pytest.fixture
def mock_auth_error_response(mock_http_response_factory) -> Mock:
    """Mock 401 authentication error response."""
    return mock_http_response_factory(
        status_code=401,
        json_data={
            "error": "unauthorized",
            "message": "Invalid or expired token",
        },
    )


@pytest.fixture
def mock_forbidden_response(mock_http_response_factory) -> Mock:
    """Mock 403 forbidden response."""
    return mock_http_response_factory(
        status_code=403,
        json_data={
            "error": "forbidden",
            "message": "Access denied",
        },
    )


@pytest.fixture
def mock_requests_session(mock_successful_agent_response):
    """Mock requests.Session for HTTP testing."""
    session = Mock()
    session.post.return_value = mock_successful_agent_response
    session.close.return_value = None
    return session


@pytest.fixture
def captured_http_requests():
    """Fixture to capture HTTP requests made during tests."""

    class RequestCapture:
        def __init__(self):
            self.requests = []

        def capture(self, url, **kwargs):
            self.requests.append(
                {
                    "url": url,
                    "method": "POST",
                    "headers": kwargs.get("headers", {}),
                    "json": kwargs.get("json", {}),
                    "timeout": kwargs.get("timeout"),
                }
            )
            # Return a successful mock response
            response = Mock()
            response.status_code = 200
            response.json.return_value = {"output": "Success"}
            response.text = '{"output": "Success"}'
            return response

        def get_last_request(self):
            return self.requests[-1] if self.requests else None

        def get_authorization_header(self, index: int = -1):
            if self.requests:
                return self.requests[index].get("headers", {}).get("Authorization")
            return None

        def verify_jwt_forwarded(self, expected_token: str) -> bool:
            """Verify that the JWT was forwarded correctly."""
            auth_header = self.get_authorization_header()
            if auth_header:
                return auth_header == f"Bearer {expected_token}"
            return False

    return RequestCapture()


@pytest.fixture
def mock_agent_endpoints(monkeypatch):
    """Set up mock agent endpoint URLs for testing."""
    endpoints = {
        "PROFILE_AGENT_URL": "http://localhost:8001/invoke",
        "ACCOUNTS_AGENT_URL": "http://localhost:8002/invoke",
    }

    for key, value in endpoints.items():
        monkeypatch.setenv(key, value)

    return endpoints


# Cleanup fixtures
@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks after each test."""
    yield
    # Cleanup code if needed
