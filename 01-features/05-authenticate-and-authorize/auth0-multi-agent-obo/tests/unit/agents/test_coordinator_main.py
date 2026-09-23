# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Coordinator Agent main.py entry point.

Note: These tests require the bedrock_agentcore SDK and coordinator module dependencies
which are only available in the AgentCore container runtime environment.
Tests are skipped when the SDK is not installed.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta

# Add agents directory to path for imports
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "coordinator"),
)

# Check if bedrock_agentcore SDK is available
try:
    import bedrock_agentcore.runtime

    HAS_AGENTCORE_SDK = True
except ImportError:
    HAS_AGENTCORE_SDK = False

pytestmark = pytest.mark.skipif(not HAS_AGENTCORE_SDK, reason="bedrock_agentcore SDK not available (container-only)")


class TestExtractUserContext:
    """Tests for extract_user_context_from_payload function."""

    def test_extracts_from_payload_claims(self):
        """Test extraction from payload claims field."""
        from main import extract_user_context_from_payload

        payload = {
            "claims": {
                "sub": "auth0|123456",
                "email": "test@example.com",
                "https://agentcore.example.com/customer_id": "CUST-001",
            }
        }

        result = extract_user_context_from_payload(payload, None)

        assert result["user_id"] == "auth0|123456"
        assert result["email"] == "test@example.com"
        assert result["customer_id"] == "CUST-001"

    def test_extracts_from_access_token(self):
        """Test extraction from access_token in payload."""
        from main import extract_user_context_from_payload
        import jwt

        # Create a test JWT
        payload_data = {
            "sub": "auth0|token-user",
            "email": "token@example.com",
            "https://agentcore.example.com/customer_id": "CUST-TOKEN",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
            "iss": "https://issuer",
        }
        token = jwt.encode(payload_data, "secret", algorithm="HS256")

        payload = {"access_token": token}

        result = extract_user_context_from_payload(payload, None)

        assert result["user_id"] == "auth0|token-user"

    def test_handles_missing_claims(self):
        """Test handling of missing claims."""
        from main import extract_user_context_from_payload

        payload = {}

        result = extract_user_context_from_payload(payload, None)

        # Should return unknown values
        assert result["user_id"] == "unknown"

    def test_handles_none_payload(self):
        """Test handling of None payload."""
        from main import extract_user_context_from_payload

        result = extract_user_context_from_payload(None, None)

        assert result["user_id"] == "unknown"


class TestExtractUserContextPermissionsVsScope:
    """Tests for permissions-first logic in extract_user_context_from_payload.

    Verifies that the coordinator reads the RBAC `permissions` claim as the
    primary authorization source, with fallback to `scope` for non-Auth0 IdPs.
    """

    def test_permissions_claim_takes_precedence_over_scope(self):
        """When JWT has both `permissions` and `scope`, permissions wins."""
        from main import extract_user_context_from_payload
        import jwt

        payload_data = {
            "sub": "auth0|rbac-user",
            "email": "rbac@example.com",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
            "iss": "https://your-tenant.auth0.com/",
            # scope has ALL scopes (client-level)
            "scope": (
                "openid profile email "
                "profile:personal:read profile:personal:write "
                "profile:preferences:read profile:preferences:write "
                "accounts:savings:read accounts:savings:write "
                "accounts:transaction:read "
                "accounts:credit:read accounts:credit:write "
                "accounts:investment:read"
            ),
            # permissions has only profile scopes (RBAC-restricted)
            "permissions": [
                "openid",
                "profile",
                "email",
                "profile:personal:read",
                "profile:personal:write",
                "profile:preferences:read",
                "profile:preferences:write",
            ],
        }
        token = jwt.encode(payload_data, "secret", algorithm="HS256")

        payload = {"access_token": token}
        result = extract_user_context_from_payload(payload, None)

        # permissions claim should be used, NOT scope
        assert "accounts:savings:read" not in result["permissions"]
        assert "profile:personal:read" in result["permissions"]
        assert len(result["permissions"]) == 7  # only profile-related

    def test_falls_back_to_scope_when_no_permissions_claim(self):
        """When JWT has no `permissions` claim, falls back to `scope`."""
        from main import extract_user_context_from_payload
        import jwt

        payload_data = {
            "sub": "okta|non-auth0-user",
            "email": "okta@example.com",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
            "iss": "https://okta.example.com/",
            # Only scope, no permissions claim (non-Auth0 IdP)
            "scope": "openid profile email accounts:savings:read",
        }
        token = jwt.encode(payload_data, "secret", algorithm="HS256")

        payload = {"access_token": token}
        result = extract_user_context_from_payload(payload, None)

        # Should fall back to scope
        assert "openid" in result["permissions"]
        assert "accounts:savings:read" in result["permissions"]

    def test_empty_permissions_claim_falls_back_to_scope(self):
        """When JWT has empty `permissions` list, falls back to `scope`."""
        from main import extract_user_context_from_payload
        import jwt

        payload_data = {
            "sub": "auth0|empty-perms-user",
            "email": "empty@example.com",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
            "iss": "https://your-tenant.auth0.com/",
            "scope": "openid profile email accounts:savings:read",
            "permissions": [],  # Empty list
        }
        token = jwt.encode(payload_data, "secret", algorithm="HS256")

        payload = {"access_token": token}
        result = extract_user_context_from_payload(payload, None)

        # Empty permissions should trigger scope fallback
        assert "openid" in result["permissions"]
        assert "accounts:savings:read" in result["permissions"]

    def test_permissions_claim_as_list_preserved(self):
        """When permissions is a list, it's preserved as-is."""
        from main import extract_user_context_from_payload
        import jwt

        expected_permissions = [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "accounts:savings:read",
        ]

        payload_data = {
            "sub": "auth0|list-perms-user",
            "email": "list@example.com",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
            "iss": "https://your-tenant.auth0.com/",
            "scope": "openid profile email everything:read",
            "permissions": expected_permissions,
        }
        token = jwt.encode(payload_data, "secret", algorithm="HS256")

        payload = {"access_token": token}
        result = extract_user_context_from_payload(payload, None)

        # Should match the permissions list exactly (before roles extension)
        for perm in expected_permissions:
            assert perm in result["permissions"]


class TestValidateUserAuthorization:
    """Tests for validate_user_authorization function."""

    def test_valid_authorization(self):
        """Test successful authorization validation."""
        from main import validate_user_authorization

        user_context = {
            "user_id": "auth0|123456",
            "email": "test@example.com",
            "email_verified": True,
            "customer_id": "CUST-001",
            "permissions": ["profile:personal:read"],
        }

        result = validate_user_authorization(user_context)

        assert result is True

    def test_unknown_user_unauthorized(self):
        """Test that unknown user is unauthorized."""
        from main import validate_user_authorization

        user_context = {
            "user_id": "unknown",
            "email": "unknown",
            "customer_id": "unknown",
        }

        result = validate_user_authorization(user_context)

        assert result is False

    def test_authenticated_user_authorized_without_explicit_permissions(self):
        """Test that authenticated user (valid user_id) is authorized even without permissions.

        validate_user_authorization grants access to any authenticated user
        (user_id present and not 'unknown') since the JWT is already validated
        by AgentCore's customJWTAuthorizer.
        """
        from main import validate_user_authorization

        user_context = {
            "user_id": "auth0|123456",
            "email": "test@example.com",
            "customer_id": "CUST-001",
            "permissions": [],  # No permissions, but user is authenticated
        }

        result = validate_user_authorization(user_context)

        # Authenticated users pass -- AgentCore JWT authorizer already validated
        assert result is True


class TestInvokeEntrypoint:
    """Tests for the invoke entrypoint function."""

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_user_authorization")
    @patch("main.extract_user_context_from_payload")
    async def test_successful_invocation(self, mock_extract, mock_validate, mock_create):
        """Test successful invocation."""
        from main import invoke

        mock_extract.return_value = {
            "user_id": "user-123",
            "customer_id": "CUST001",
            "email": "test@example.com",
            "permissions": ["profile:personal:read"],
            "access_token": "",
        }

        mock_validate.return_value = True

        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(return_value={"output": "Agent response"})
        mock_create.return_value = mock_agent

        payload = {"prompt": "Hello"}

        result = await invoke(payload, None)

        assert result["status"] == "success"

    @pytest.mark.asyncio
    @patch("main.validate_user_authorization")
    @patch("main.extract_user_context_from_payload")
    async def test_unauthorized_user(self, mock_extract, mock_validate):
        """Test that unauthorized user gets error response."""
        from main import invoke

        mock_extract.return_value = {
            "user_id": "unknown",
            "customer_id": "unknown",
            "permissions": [],
            "access_token": "",
        }

        mock_validate.return_value = False

        payload = {"prompt": "Hello"}

        result = await invoke(payload, None)

        assert result["status"] == "error"
        assert "AUTHORIZATION" in result["error"]

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_user_authorization")
    @patch("main.extract_user_context_from_payload")
    async def test_agent_exception_handling(self, mock_extract, mock_validate, mock_create):
        """Test that agent exceptions are caught."""
        from main import invoke

        mock_extract.return_value = {
            "user_id": "user-123",
            "customer_id": "CUST001",
            "permissions": ["profile:personal:read"],
            "access_token": "",
        }

        mock_validate.return_value = True

        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(side_effect=Exception("Agent crashed"))
        mock_create.return_value = mock_agent

        payload = {"prompt": "Hello"}

        result = await invoke(payload, None)

        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_empty_payload(self):
        """Test with empty payload."""
        from main import invoke

        result = await invoke({}, None)

        # Should handle gracefully
        assert "status" in result

    @pytest.mark.asyncio
    @patch("main.create_agent")
    @patch("main.validate_user_authorization")
    @patch("main.extract_user_context_from_payload")
    async def test_response_includes_metadata(self, mock_extract, mock_validate, mock_create):
        """Test that response includes required metadata."""
        from main import invoke

        mock_extract.return_value = {
            "user_id": "user-123",
            "customer_id": "CUST001",
            "permissions": ["profile:personal:read"],
            "access_token": "",
        }

        mock_validate.return_value = True

        mock_agent = MagicMock()
        mock_agent.process = AsyncMock(return_value={"output": "Response"})
        mock_create.return_value = mock_agent

        payload = {"prompt": "Hello", "sessionId": "session-123"}

        result = await invoke(payload, None)

        assert "trace_id" in result
        assert "session_id" in result
        assert "duration_ms" in result


class TestDecodeJwtClaims:
    """Tests for decode_jwt_claims helper function."""

    def test_decodes_valid_jwt(self):
        """Test decoding of valid JWT."""
        from main import decode_jwt_claims
        import jwt

        payload = {
            "sub": "auth0|123456",
            "exp": int((datetime.utcnow() + timedelta(hours=1)).timestamp()),
        }
        token = jwt.encode(payload, "secret", algorithm="HS256")

        result = decode_jwt_claims(token)

        assert result["sub"] == "auth0|123456"

    def test_handles_invalid_jwt(self):
        """Test handling of invalid JWT."""
        from main import decode_jwt_claims

        result = decode_jwt_claims("invalid.token.here")

        # Should return empty dict or handle gracefully
        assert isinstance(result, dict)

    def test_handles_empty_token(self):
        """Test handling of empty token."""
        from main import decode_jwt_claims

        result = decode_jwt_claims("")

        assert isinstance(result, dict)

    def test_handles_none_token(self):
        """Test handling of None token."""
        from main import decode_jwt_claims

        # Should not raise exception
        try:
            result = decode_jwt_claims(None)
            assert isinstance(result, dict)
        except (TypeError, AttributeError):
            # Also acceptable to raise on None
            pass
