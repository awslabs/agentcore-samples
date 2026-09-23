# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Customer Profile Agent auth_validator module.
"""

import os
import importlib.util


# Load module from specific path to avoid conflicts
def load_module_from_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_module_path = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "agents",
    "customer_profile",
    "auth_validator.py",
)
_auth_validator = load_module_from_path("profile_auth_validator", _module_path)

validate_forwarded_claims = _auth_validator.validate_forwarded_claims
authorize_profile_access = _auth_validator.authorize_profile_access
get_audit_context = _auth_validator.get_audit_context


class TestValidateForwardedClaims:
    """Tests for validate_forwarded_claims function."""

    def test_valid_claims_with_namespaced_customer_id(self):
        """Test validation passes with properly namespaced customer_id."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
            "https://agentcore.example.com/customer_id": "CUST-001",
            "https://agentcore.example.com/customer_number": "CN-12345",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is True
        assert result["user_id"] == "auth0|123456"
        assert result["customer_id"] == "CUST-001"
        assert result["customer_number"] == "CN-12345"

    def test_valid_claims_with_direct_customer_id(self):
        """Test validation passes with direct customer_id claim."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
            "customer_id": "CUST-002",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is True
        assert result["customer_id"] == "CUST-002"

    def test_valid_m2m_token_fallback(self):
        """Test M2M token (client_credentials) falls back to test customer."""
        claims = {
            "sub": "abc123@clients",  # M2M token indicator
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is True
        assert result["customer_id"] == "CUST001"  # Default test customer

    def test_empty_claims_returns_error(self):
        """Test that empty claims dict returns error."""
        result = validate_forwarded_claims({})

        assert result["valid"] is False
        assert "Missing" in result["error"]  # Could be "Missing required claims" or "Missing authentication claims"

    def test_none_claims_returns_error(self):
        """Test that None claims returns error."""
        result = validate_forwarded_claims(None)

        assert result["valid"] is False
        assert "Missing authentication claims" in result["error"]

    def test_missing_sub_claim(self):
        """Test that missing 'sub' claim returns error."""
        claims = {
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "sub" in result["error"]

    def test_missing_exp_claim(self):
        """Test that missing 'exp' claim returns error."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "iss": "https://example.auth0.com/",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "exp" in result["error"]

    def test_missing_iss_claim(self):
        """Test that missing 'iss' claim returns error."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "iss" in result["error"]

    def test_missing_aud_claim(self):
        """Test that missing 'aud' claim returns error."""
        claims = {
            "sub": "auth0|123456",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "aud" in result["error"]

    def test_empty_issuer_returns_error(self):
        """Test that empty issuer string returns error."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "Invalid issuer" in result["error"]

    def test_missing_customer_id_non_m2m_returns_error(self):
        """Test that missing customer_id for non-M2M token returns error."""
        claims = {
            "sub": "auth0|123456",  # Not an M2M token
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "Missing customer identity" in result["error"]


class TestAuthorizeProfileAccess:
    """Tests for authorize_profile_access function."""

    def test_authorized_same_customer_id(self):
        """Test authorization passes when customer_id matches."""
        claims = {"https://agentcore.example.com/customer_id": "CUST-001"}

        result = authorize_profile_access(claims, "CUST-001")

        assert result is True

    def test_authorized_direct_customer_id(self):
        """Test authorization with direct customer_id claim."""
        claims = {"customer_id": "CUST-002"}

        result = authorize_profile_access(claims, "CUST-002")

        assert result is True

    def test_unauthorized_different_customer_id(self):
        """Test authorization fails when customer_id doesn't match."""
        claims = {"https://agentcore.example.com/customer_id": "CUST-001"}

        result = authorize_profile_access(claims, "CUST-999")

        assert result is False

    def test_unauthorized_missing_customer_id(self):
        """Test authorization fails when customer_id is missing."""
        claims = {"sub": "auth0|123456"}

        result = authorize_profile_access(claims, "CUST-001")

        assert result is False

    def test_unauthorized_empty_claims(self):
        """Test authorization fails with empty claims."""
        result = authorize_profile_access({}, "CUST-001")

        assert result is False


class TestGetAuditContext:
    """Tests for get_audit_context function."""

    def test_extracts_all_fields(self):
        """Test that all audit fields are extracted."""
        claims = {
            "sub": "auth0|123456",
            "email": "test@example.com",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = get_audit_context(claims)

        assert result["sub"] == "auth0|123456"
        assert result["email"] == "test@example.com"
        assert result["customer_id"] == "CUST-001"
        assert "timestamp" in result

    def test_handles_missing_fields(self):
        """Test that missing fields return None."""
        claims = {"sub": "auth0|123456"}

        result = get_audit_context(claims)

        assert result["sub"] == "auth0|123456"
        assert result["email"] is None
        assert result["customer_id"] is None
        assert "timestamp" in result

    def test_uses_direct_customer_id_fallback(self):
        """Test fallback to direct customer_id claim."""
        claims = {"sub": "auth0|123456", "customer_id": "CUST-DIRECT"}

        result = get_audit_context(claims)

        assert result["customer_id"] == "CUST-DIRECT"
