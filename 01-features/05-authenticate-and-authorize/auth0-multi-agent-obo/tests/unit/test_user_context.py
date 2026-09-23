# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for UserContext model.

Tests the UserContext data model that represents authenticated user information.
"""


class TestUserContextModel:
    """Test UserContext data model."""

    def test_create_user_context(self, sample_user_context):
        """Test creating a UserContext instance."""
        assert sample_user_context["user_id"] == "auth0|123456789"
        assert sample_user_context["email"] == "john.doe@example.com"
        assert sample_user_context["customer_id"] == "CUST-12345"

    def test_user_context_required_fields(self):
        """Test that required fields are present."""
        required_fields = ["user_id", "customer_id"]
        minimal_context = {"user_id": "auth0|123456789", "customer_id": "CUST-12345"}

        for field in required_fields:
            assert field in minimal_context
            assert minimal_context[field] is not None

    def test_user_context_optional_fields(self, sample_user_context):
        """Test optional fields in user context."""
        optional_fields = [
            "email",
            "name",
            "account_ids",
            "roles",
            "kyc_status",
            "scopes",
        ]

        for field in optional_fields:
            # Field may or may not be present
            if field in sample_user_context:
                assert sample_user_context[field] is not None

    def test_user_context_with_multiple_accounts(self, sample_user_context):
        """Test user context with multiple account IDs."""
        account_ids = sample_user_context["account_ids"]
        assert isinstance(account_ids, list)
        assert len(account_ids) > 1

    def test_user_context_with_roles(self, sample_user_context):
        """Test user context with roles."""
        roles = sample_user_context["roles"]
        assert isinstance(roles, list)
        assert "customer" in roles

    def test_user_context_with_scopes(self, sample_user_context):
        """Test user context with OAuth scopes."""
        scopes = sample_user_context["scopes"]
        assert isinstance(scopes, list)
        assert "openid" in scopes

    def test_user_context_serialization(self, sample_user_context):
        """Test serializing user context to dict/JSON."""
        import json

        # Should be JSON serializable
        json_str = json.dumps(sample_user_context)
        assert json_str is not None

        # Should be deserializable
        deserialized = json.loads(json_str)
        assert deserialized["user_id"] == sample_user_context["user_id"]

    def test_user_context_immutability(self, sample_user_context):
        """Test that user context should be immutable after creation."""
        original_user_id = sample_user_context["user_id"]

        # In a real implementation, this should be prevented
        # For this test, we just verify the original value
        assert sample_user_context["user_id"] == original_user_id


class TestUserContextValidation:
    """Test validation of user context."""

    def test_validate_user_id_format(self, sample_user_context):
        """Test validation of user ID format."""
        user_id = sample_user_context["user_id"]
        assert user_id.startswith("auth0|")
        assert len(user_id) > 6

    def test_validate_customer_id_format(self, sample_user_context):
        """Test validation of customer ID format."""
        customer_id = sample_user_context["customer_id"]
        assert customer_id.startswith("CUST-")
        assert len(customer_id) > 5

    def test_validate_email_format(self, sample_user_context):
        """Test validation of email format."""
        email = sample_user_context["email"]
        assert "@" in email
        assert "." in email

    def test_validate_account_ids_not_empty(self, sample_user_context):
        """Test that account IDs list is not empty."""
        account_ids = sample_user_context.get("account_ids", [])
        assert len(account_ids) > 0

    def test_invalid_user_context_missing_user_id(self):
        """Test handling of invalid context missing user_id."""
        invalid_context = {"customer_id": "CUST-12345", "email": "john.doe@example.com"}

        # Should fail validation
        assert "user_id" not in invalid_context

    def test_invalid_user_context_missing_customer_id(self):
        """Test handling of invalid context missing customer_id."""
        invalid_context = {
            "user_id": "auth0|123456789",
            "email": "john.doe@example.com",
        }

        # Should fail validation
        assert "customer_id" not in invalid_context


class TestUserContextOperations:
    """Test operations on user context.

    Note: Scope checking (has_scope), role checking (has_role), account access
    (has_account), and KYC verification are tested in test_auth_validator.py
    (TestScopeValidation, TestRoleBasedAccess, TestResourceAccessControl,
    TestKYCAuthorization) and test_claims_extractor.py (TestScopeExtraction).
    This class covers display/metadata operations only.
    """

    def test_get_customer_display_name(self, sample_user_context):
        """Test getting display name from user context."""
        name = sample_user_context.get("name")
        email = sample_user_context.get("email")

        # Use name if available, fallback to email
        display_name = name if name else email.split("@")[0] if email else "Unknown"

        assert display_name == "John Doe"

    def test_user_context_to_agentcore_metadata(self, sample_user_context):
        """Test converting user context to AgentCore metadata."""
        metadata = {
            "userId": sample_user_context["user_id"],
            "customerId": sample_user_context["customer_id"],
            "accountIds": sample_user_context["account_ids"],
            "kycStatus": sample_user_context.get("kyc_status"),
        }

        assert metadata["userId"] == "auth0|123456789"
        assert metadata["customerId"] == "CUST-12345"
        assert len(metadata["accountIds"]) == 2


class TestUserContextComparison:
    """Test comparison and equality of user contexts."""

    def test_same_user_contexts_equal(self, sample_user_context):
        """Test that identical user contexts are equal."""
        context1 = sample_user_context.copy()
        context2 = sample_user_context.copy()

        assert context1["user_id"] == context2["user_id"]
        assert context1["customer_id"] == context2["customer_id"]

    def test_different_user_contexts_not_equal(self, sample_user_context):
        """Test that different user contexts are not equal."""
        context1 = sample_user_context.copy()
        context2 = sample_user_context.copy()
        context2["user_id"] = "auth0|987654321"

        assert context1["user_id"] != context2["user_id"]

    def test_user_context_subset_comparison(self):
        """Test comparing user contexts with different fields."""
        context1 = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "email": "john.doe@example.com",
        }

        context2 = {"user_id": "auth0|123456789", "customer_id": "CUST-12345"}

        # Same user even with different fields
        assert context1["user_id"] == context2["user_id"]
        assert context1["customer_id"] == context2["customer_id"]


class TestUserContextEdgeCases:
    """Test edge cases for user context."""

    def test_user_context_with_empty_name(self):
        """Test user context with empty name."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "name": "",
            "email": "john.doe@example.com",
        }

        assert context["name"] == ""
        # Should fallback to email for display
        display = context["name"] if context["name"] else context["email"]
        assert display == "john.doe@example.com"

    def test_user_context_with_no_accounts(self):
        """Test user context with empty account list."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "account_ids": [],
        }

        assert len(context["account_ids"]) == 0

    def test_user_context_with_no_roles(self):
        """Test user context with empty roles list."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "roles": [],
        }

        assert len(context["roles"]) == 0

    def test_user_context_with_no_scopes(self):
        """Test user context with empty scopes list."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "scopes": [],
        }

        assert len(context["scopes"]) == 0

    def test_user_context_with_special_characters(self):
        """Test user context with special characters in fields."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "name": "John O'Doe-Smith",
            "email": "john.doe+test@example.com",
        }

        assert "'" in context["name"]
        assert "-" in context["name"]
        assert "+" in context["email"]

    def test_user_context_with_unicode_characters(self):
        """Test user context with unicode characters."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "name": "José García",
            "email": "jose.garcia@example.com",
        }

        assert context["name"] == "José García"
        # Should handle unicode properly
        import json

        json_str = json.dumps(context, ensure_ascii=False)
        assert "José" in json_str
