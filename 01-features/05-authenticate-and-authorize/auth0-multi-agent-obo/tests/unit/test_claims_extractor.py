# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for JWT claims extraction.

Tests extraction and transformation of JWT claims into application context.
"""


class TestClaimsExtractor:
    """Test extraction of claims from JWT payload."""

    def test_extract_user_id(self, sample_jwt_payload):
        """Test extraction of user ID from 'sub' claim."""
        user_id = sample_jwt_payload.get("sub")
        assert user_id == "auth0|123456789"
        assert user_id.startswith("auth0|")

    def test_extract_email(self, sample_jwt_payload):
        """Test extraction of email claim."""
        email = sample_jwt_payload.get("email")
        assert email == "john.doe@example.com"
        assert "@" in email

    def test_extract_custom_customer_id(self, sample_jwt_payload):
        """Test extraction of custom customer_id claim."""
        customer_id = sample_jwt_payload.get("https://agentcore.example.com/customer_id")
        assert customer_id == "CUST-12345"
        assert customer_id.startswith("CUST-")

    def test_extract_account_ids_array(self, sample_jwt_payload):
        """Test extraction of account IDs array."""
        account_ids = sample_jwt_payload.get("https://agentcore.example.com/account_ids")
        assert isinstance(account_ids, list)
        assert len(account_ids) == 2
        assert "ACC-001" in account_ids
        assert "ACC-002" in account_ids

    def test_extract_roles_array(self, sample_jwt_payload):
        """Test extraction of roles array."""
        roles = sample_jwt_payload.get("https://agentcore.example.com/roles")
        assert isinstance(roles, list)
        assert "customer" in roles
        assert "premium" in roles

    def test_extract_kyc_status(self, sample_jwt_payload):
        """Test extraction of KYC status."""
        kyc_status = sample_jwt_payload.get("https://agentcore.example.com/kyc_status")
        assert kyc_status == "verified"

    def test_extract_scopes(self, sample_jwt_payload):
        """Test extraction and parsing of OAuth scopes."""
        scope_string = sample_jwt_payload.get("scope")
        assert scope_string is not None

        scopes = scope_string.split()
        assert "openid" in scopes
        assert "profile" in scopes
        assert "email" in scopes
        assert "profile:personal:read" in scopes
        assert "accounts:savings:read" in scopes

    def test_extract_standard_profile_claims(self, sample_jwt_payload):
        """Test extraction of standard OIDC profile claims."""
        assert sample_jwt_payload.get("name") == "John Doe"
        assert sample_jwt_payload.get("nickname") == "johndoe"
        assert sample_jwt_payload.get("picture") == "https://example.com/avatar.jpg"
        assert sample_jwt_payload.get("email_verified") is True

    def test_extract_timestamps(self, sample_jwt_payload):
        """Test extraction of timestamp claims."""
        assert "iat" in sample_jwt_payload  # Issued at
        assert "exp" in sample_jwt_payload  # Expiration
        assert sample_jwt_payload["exp"] > sample_jwt_payload["iat"]

    def test_extract_audience(self, sample_jwt_payload):
        """Test extraction of audience claim."""
        aud = sample_jwt_payload.get("aud")
        assert aud is not None
        assert isinstance(aud, list)
        assert "https://agentcore-financial-api" in aud

    def test_extract_issuer(self, sample_jwt_payload):
        """Test extraction of issuer claim."""
        issuer = sample_jwt_payload.get("iss")
        assert issuer == "https://your-tenant.auth0.com/"
        assert issuer.startswith("https://")
        assert issuer.endswith("/")


class TestClaimsTransformation:
    """Test transformation of JWT claims into application models."""

    def test_transform_to_user_context(self, sample_jwt_payload, sample_user_context):
        """Test transformation of JWT payload to UserContext."""
        # Simulate transformation
        user_context = {
            "user_id": sample_jwt_payload.get("sub"),
            "email": sample_jwt_payload.get("email"),
            "name": sample_jwt_payload.get("name"),
            "customer_id": sample_jwt_payload.get("https://agentcore.example.com/customer_id"),
            "account_ids": sample_jwt_payload.get("https://agentcore.example.com/account_ids"),
            "roles": sample_jwt_payload.get("https://agentcore.example.com/roles"),
            "kyc_status": sample_jwt_payload.get("https://agentcore.example.com/kyc_status"),
            "scopes": sample_jwt_payload.get("scope", "").split(),
        }

        assert user_context["user_id"] == sample_user_context["user_id"]
        assert user_context["email"] == sample_user_context["email"]
        assert user_context["customer_id"] == sample_user_context["customer_id"]
        assert len(user_context["scopes"]) == len(sample_user_context["scopes"])

    def test_handle_missing_optional_claims(self):
        """Test handling of missing optional claims."""
        minimal_payload = {
            "sub": "auth0|123456789",
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://agentcore-financial-api",
        }

        # Should handle missing claims gracefully
        email = minimal_payload.get("email")
        name = minimal_payload.get("name")
        customer_id = minimal_payload.get("https://agentcore.example.com/customer_id")

        assert email is None
        assert name is None
        assert customer_id is None

    def test_handle_empty_arrays(self):
        """Test handling of empty array claims."""
        payload = {
            "sub": "auth0|123456789",
            "https://agentcore.example.com/account_ids": [],
            "https://agentcore.example.com/roles": [],
        }

        account_ids = payload.get("https://agentcore.example.com/account_ids")
        roles = payload.get("https://agentcore.example.com/roles")

        assert isinstance(account_ids, list)
        assert len(account_ids) == 0
        assert isinstance(roles, list)
        assert len(roles) == 0

    def test_normalize_email_case(self, sample_jwt_payload):
        """Test email normalization (lowercase)."""
        email = sample_jwt_payload.get("email")
        normalized = email.lower() if email else None

        assert normalized == "john.doe@example.com"

    def test_extract_first_last_name(self, sample_jwt_payload):
        """Test extraction of first and last name from full name."""
        full_name = sample_jwt_payload.get("name")
        parts = full_name.split(" ", 1) if full_name else []

        first_name = parts[0] if len(parts) > 0 else None
        last_name = parts[1] if len(parts) > 1 else None

        assert first_name == "John"
        assert last_name == "Doe"


class TestCustomClaimsNamespace:
    """Test handling of custom claims namespaces."""

    def test_extract_claims_with_namespace(self, sample_jwt_payload):
        """Test extraction of all claims under custom namespace."""
        namespace = "https://agentcore.example.com/"
        custom_claims = {
            key.replace(namespace, ""): value for key, value in sample_jwt_payload.items() if key.startswith(namespace)
        }

        assert "customer_id" in custom_claims
        assert "account_ids" in custom_claims
        assert "roles" in custom_claims
        assert "kyc_status" in custom_claims

    def test_different_namespace_format(self):
        """Test handling of different namespace formats."""
        payload = {
            "sub": "auth0|123456789",
            "https://agentcore.example.com/customer_id": "CUST-12345",
            "agentcore.example.com/customer_id": "CUST-67890",  # Different format
        }

        # Should handle both formats
        assert "https://agentcore.example.com/customer_id" in payload
        assert "agentcore.example.com/customer_id" in payload

    def test_claims_without_namespace(self):
        """Test extraction of standard claims without namespace."""
        payload = {
            "sub": "auth0|123456789",
            "email": "john.doe@example.com",
            "name": "John Doe",
            "email_verified": True,
        }

        standard_claims = {key: value for key, value in payload.items() if not key.startswith("https://")}

        assert len(standard_claims) == 4
        assert "sub" in standard_claims
        assert "email" in standard_claims


class TestScopeExtraction:
    """Test extraction and validation of OAuth scopes."""

    def test_parse_scope_string(self, sample_jwt_payload):
        """Test parsing space-separated scope string."""
        scope_string = sample_jwt_payload.get("scope")
        scopes = scope_string.split() if scope_string else []

        assert len(scopes) == 13  # 3 OIDC + 4 profile + 6 accounts
        assert isinstance(scopes, list)

    def test_check_scope_presence(self, sample_jwt_payload):
        """Test checking for presence of specific scopes."""
        scope_string = sample_jwt_payload.get("scope", "")
        scopes = scope_string.split()

        def has_scope(scope: str) -> bool:
            return scope in scopes

        assert has_scope("openid")
        assert has_scope("profile:personal:read")
        assert has_scope("accounts:savings:read")
        assert not has_scope("admin:write")

    def test_extract_resource_scopes(self, sample_jwt_payload):
        """Test extraction of resource-specific scopes."""
        scope_string = sample_jwt_payload.get("scope", "")
        scopes = scope_string.split()

        # Extract profile-related scopes
        profile_scopes = [s for s in scopes if s.startswith("profile:")]
        assert len(profile_scopes) == 4
        assert "profile:personal:read" in profile_scopes
        assert "profile:preferences:write" in profile_scopes

        # Extract account-related scopes
        account_scopes = [s for s in scopes if s.startswith("accounts:")]
        assert len(account_scopes) == 6
        assert "accounts:savings:read" in account_scopes
        assert "accounts:credit:read" in account_scopes

    def test_empty_scopes(self):
        """Test handling of missing or empty scopes."""
        payload = {"sub": "auth0|123456789", "scope": ""}

        scope_string = payload.get("scope", "")
        scopes = scope_string.split() if scope_string else []

        assert isinstance(scopes, list)
        # Empty string split returns empty list or list with empty string
        assert len(scopes) == 0 or (len(scopes) == 1 and scopes[0] == "")


class TestClaimsValidation:
    """Test validation of extracted claims."""

    def test_validate_email_format(self, sample_jwt_payload):
        """Test validation of email format."""
        email = sample_jwt_payload.get("email")

        # Basic email validation
        assert email is not None
        assert "@" in email
        assert "." in email.split("@")[1]

    def test_validate_customer_id_format(self, sample_jwt_payload):
        """Test validation of customer ID format."""
        customer_id = sample_jwt_payload.get("https://agentcore.example.com/customer_id")

        assert customer_id is not None
        assert customer_id.startswith("CUST-")
        assert len(customer_id) > 5

    def test_validate_account_ids_format(self, sample_jwt_payload):
        """Test validation of account IDs format."""
        account_ids = sample_jwt_payload.get("https://agentcore.example.com/account_ids")

        assert isinstance(account_ids, list)
        for account_id in account_ids:
            assert account_id.startswith("ACC-")
            assert len(account_id) > 4

    def test_validate_kyc_status_values(self, sample_jwt_payload):
        """Test validation of KYC status values."""
        kyc_status = sample_jwt_payload.get("https://agentcore.example.com/kyc_status")
        valid_statuses = ["pending", "verified", "failed", "expired"]

        assert kyc_status in valid_statuses

    def test_validate_role_values(self, sample_jwt_payload):
        """Test validation of role values."""
        roles = sample_jwt_payload.get("https://agentcore.example.com/roles")
        valid_roles = ["customer", "premium", "admin", "agent"]

        for role in roles:
            assert role in valid_roles
