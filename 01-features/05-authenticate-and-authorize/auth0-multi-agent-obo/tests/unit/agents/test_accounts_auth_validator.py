# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for Accounts Agent auth_validator module.
"""

import sys
import os

# Add agents directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "agents", "accounts"))

from auth_validator import validate_forwarded_claims, check_account_access


class TestValidateForwardedClaims:
    """Tests for validate_forwarded_claims function."""

    def test_valid_claims(self):
        """Test validation passes with all required claims."""
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

    def test_missing_multiple_claims(self):
        """Test error message includes all missing claims."""
        claims = {"https://agentcore.example.com/customer_id": "CUST-001"}

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        # Should mention missing claims

    def test_empty_issuer(self):
        """Test that empty issuer returns error."""
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

    def test_missing_customer_id(self):
        """Test that missing customer_id returns error."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is False
        assert "Missing customer identity" in result["error"]

    def test_valid_without_customer_number(self):
        """Test validation passes even without customer_number."""
        claims = {
            "sub": "auth0|123456",
            "aud": "https://agentcore-financial-api",
            "exp": 9999999999,
            "iss": "https://example.auth0.com/",
            "https://agentcore.example.com/customer_id": "CUST-001",
        }

        result = validate_forwarded_claims(claims)

        assert result["valid"] is True
        assert result["customer_number"] is None


class TestCheckAccountAccess:
    """Tests for check_account_access function."""

    def test_authorized_regular_account(self):
        """Test authorization passes for regular account numbers."""
        claims = {"sub": "auth0|123456"}

        result = check_account_access("CUST-001", "123456789", claims)

        assert result["authorized"] is True
        assert result["access_level"] == "owner"

    def test_unauthorized_account_starting_with_99(self):
        """Test authorization fails for accounts starting with 99."""
        claims = {"sub": "auth0|123456"}

        result = check_account_access("CUST-001", "99123456", claims)

        assert result["authorized"] is False
        assert "does not have access" in result["reason"]

    def test_various_valid_account_numbers(self):
        """Test various valid account numbers."""
        claims = {"sub": "auth0|123456"}

        valid_accounts = ["12345678", "00000001", "98765432", "11111111"]

        for account in valid_accounts:
            result = check_account_access("CUST-001", account, claims)
            assert result["authorized"] is True, f"Account {account} should be authorized"

    def test_edge_case_account_99_in_middle(self):
        """Test account with 99 in middle is still authorized."""
        claims = {"sub": "auth0|123456"}

        result = check_account_access("CUST-001", "12399456", claims)

        assert result["authorized"] is True
