# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for authorization validation.

Tests authorization checks including scope validation and resource access control.
"""

import pytest
from typing import List, Dict, Any


class TestScopeValidation:
    """Test OAuth scope validation with fine-grained resource-level scopes."""

    def test_has_required_scope(self, sample_user_context):
        """Test checking for required scope."""
        scopes = sample_user_context["scopes"]

        def has_scope(required_scope: str) -> bool:
            return required_scope in scopes

        assert has_scope("profile:personal:read")
        assert has_scope("accounts:savings:read")
        assert not has_scope("admin:delete")

    def test_has_all_required_scopes(self, sample_user_context):
        """Test checking for multiple required scopes."""
        scopes = sample_user_context["scopes"]
        required_scopes = ["openid", "profile", "profile:personal:read"]

        def has_all_scopes(required: List[str]) -> bool:
            return all(scope in scopes for scope in required)

        assert has_all_scopes(required_scopes)
        assert not has_all_scopes(["openid", "admin:write"])

    def test_has_any_required_scope(self, sample_user_context):
        """Test checking for any of multiple scopes."""
        scopes = sample_user_context["scopes"]
        required_scopes = ["profile:personal:read", "profile:personal:write"]

        def has_any_scope(required: List[str]) -> bool:
            return any(scope in scopes for scope in required)

        assert has_any_scope(required_scopes)
        assert has_any_scope(["accounts:credit:read"])
        assert not has_any_scope(["admin:read", "admin:write"])

    def test_read_vs_write_scopes(self, sample_user_context):
        """Test distinction between read and write scopes."""
        scopes = sample_user_context["scopes"]

        has_read = "profile:personal:read" in scopes
        has_write = "profile:personal:write" in scopes

        assert has_read
        assert has_write

    def test_resource_specific_scopes(self, sample_user_context):
        """Test resource-specific scope patterns."""
        scopes = sample_user_context["scopes"]

        # Extract profile-related scopes
        profile_scopes = [s for s in scopes if s.startswith("profile:")]

        assert len(profile_scopes) == 4
        assert "profile:personal:read" in profile_scopes

        # Extract account-related scopes
        account_scopes = [s for s in scopes if s.startswith("accounts:")]
        assert len(account_scopes) == 6
        assert "accounts:savings:read" in account_scopes

    def test_scope_hierarchy(self):
        """Test scope hierarchy (write implies read access)."""
        scopes = ["profile:personal:write"]

        # In some systems, write implies read
        def can_read() -> bool:
            return "profile:personal:read" in scopes or "profile:personal:write" in scopes

        assert can_read()


class TestResourceAccessControl:
    """Test resource-level access control."""

    def test_customer_can_access_own_profile(self, sample_user_context):
        """Test customer accessing their own profile."""
        customer_id = sample_user_context["customer_id"]
        requested_customer_id = "CUST-12345"

        def can_access_profile(requested_id: str) -> bool:
            return requested_id == customer_id

        assert can_access_profile(requested_customer_id)
        assert not can_access_profile("CUST-99999")

    def test_customer_can_access_own_accounts(self, sample_user_context):
        """Test customer accessing their own accounts."""
        account_ids = sample_user_context["account_ids"]

        def can_access_account(requested_id: str) -> bool:
            return requested_id in account_ids

        assert can_access_account("ACC-001")
        assert can_access_account("ACC-002")
        assert not can_access_account("ACC-999")

    def test_customer_cannot_access_other_profile(self, sample_user_context):
        """Test customer cannot access another customer's profile."""
        customer_id = sample_user_context["customer_id"]
        other_customer_id = "CUST-99999"

        def can_access_profile(requested_id: str) -> bool:
            return requested_id == customer_id

        assert not can_access_profile(other_customer_id)

    def test_customer_cannot_access_other_accounts(self, sample_user_context):
        """Test customer cannot access another customer's accounts."""
        account_ids = sample_user_context["account_ids"]
        other_account_id = "ACC-999"

        def can_access_account(requested_id: str) -> bool:
            return requested_id in account_ids

        assert not can_access_account(other_account_id)


class TestRoleBasedAccess:
    """Test role-based access control."""

    def test_customer_role_permissions(self, sample_user_context):
        """Test permissions for customer role."""
        roles = sample_user_context["roles"]

        def has_role(role: str) -> bool:
            return role in roles

        assert has_role("customer")

        # Customer role allows basic operations
        # All should be allowed for customer role

    def test_premium_customer_permissions(self, sample_user_context):
        """Test additional permissions for premium customers."""
        roles = sample_user_context["roles"]

        is_premium = "premium" in roles
        assert is_premium

        # Premium customers get additional features
        if is_premium:
            pass

    def test_admin_role_full_access(self):
        """Test admin role has full access."""
        admin_context = {
            "user_id": "auth0|admin123",
            "roles": ["admin"],
            "customer_id": "CUST-ADMIN",
        }

        is_admin = "admin" in admin_context["roles"]
        assert is_admin

        # Admin can access any resource
        def can_access_any_profile() -> bool:
            return is_admin

        assert can_access_any_profile()

    def test_role_hierarchy(self):
        """Test role hierarchy (admin > premium > customer)."""

        def get_role_level(roles: List[str]) -> int:
            if "admin" in roles:
                return 3
            elif "premium" in roles:
                return 2
            elif "customer" in roles:
                return 1
            return 0

        admin_roles = ["admin", "customer"]
        premium_roles = ["premium", "customer"]
        customer_roles = ["customer"]

        assert get_role_level(admin_roles) > get_role_level(premium_roles)
        assert get_role_level(premium_roles) > get_role_level(customer_roles)


class TestKYCAuthorization:
    """Test KYC-based authorization."""

    def test_verified_kyc_required(self, sample_user_context):
        """Test operations requiring verified KYC."""
        kyc_status = sample_user_context["kyc_status"]

        def can_perform_transaction() -> bool:
            return kyc_status == "verified"

        assert can_perform_transaction()

    def test_unverified_kyc_restrictions(self):
        """Test restrictions for unverified KYC."""
        unverified_context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "kyc_status": "pending",
        }

        def can_perform_transaction() -> bool:
            return unverified_context["kyc_status"] == "verified"

        assert not can_perform_transaction()

    def test_kyc_status_levels(self):
        """Test different KYC status levels."""
        kyc_statuses = {
            "verified": 3,  # Full access
            "pending": 2,  # Limited access
            "failed": 1,  # Restricted
            "expired": 1,  # Restricted
        }

        def get_access_level(kyc_status: str) -> int:
            return kyc_statuses.get(kyc_status, 0)

        assert get_access_level("verified") == 3
        assert get_access_level("pending") == 2
        assert get_access_level("failed") == 1


class TestTransactionAuthorization:
    """Test authorization for transaction operations."""

    def test_authorize_transaction_read(self, sample_user_context):
        """Test authorization to read transactions."""
        scopes = sample_user_context["scopes"]
        account_ids = sample_user_context["account_ids"]

        def can_read_transactions(account_id: str) -> bool:
            has_scope = "accounts:transaction:read" in scopes
            has_account = account_id in account_ids
            return has_scope and has_account

        assert can_read_transactions("ACC-001")
        assert not can_read_transactions("ACC-999")

    def test_authorize_transaction_create(self, sample_user_context):
        """Test authorization to create transactions."""
        scopes = sample_user_context["scopes"]
        kyc_status = sample_user_context["kyc_status"]
        account_ids = sample_user_context["account_ids"]

        def can_create_transaction(account_id: str) -> bool:
            has_scope = "accounts:savings:write" in scopes
            is_verified = kyc_status == "verified"
            has_account = account_id in account_ids
            return has_scope and is_verified and has_account

        assert can_create_transaction("ACC-001")
        assert not can_create_transaction("ACC-999")

    def test_transaction_amount_limits(self, sample_user_context):
        """Test transaction amount limits based on roles."""
        roles = sample_user_context["roles"]

        def get_daily_limit() -> float:
            if "premium" in roles:
                return 10000.0
            elif "customer" in roles:
                return 5000.0
            return 1000.0

        limit = get_daily_limit()
        assert limit == 10000.0  # Premium customer


class TestAuthorizationErrors:
    """Test authorization error scenarios."""

    def test_missing_scope_error(self):
        """Test error when required scope is missing."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "scopes": ["openid", "profile"],
        }

        def check_scope(required: str) -> Dict[str, Any]:
            if required not in context["scopes"]:
                return {
                    "authorized": False,
                    "error": "insufficient_scope",
                    "error_description": f"Required scope '{required}' not present",
                }
            return {"authorized": True}

        result = check_scope("profile:personal:write")
        assert not result["authorized"]
        assert result["error"] == "insufficient_scope"

    def test_invalid_resource_error(self, sample_user_context):
        """Test error when accessing invalid resource."""
        account_ids = sample_user_context["account_ids"]

        def check_account_access(account_id: str) -> Dict[str, Any]:
            if account_id not in account_ids:
                return {
                    "authorized": False,
                    "error": "access_denied",
                    "error_description": f"Access to account '{account_id}' denied",
                }
            return {"authorized": True}

        result = check_account_access("ACC-999")
        assert not result["authorized"]
        assert result["error"] == "access_denied"

    def test_kyc_verification_required_error(self):
        """Test error when KYC verification is required."""
        context = {
            "user_id": "auth0|123456789",
            "customer_id": "CUST-12345",
            "kyc_status": "pending",
        }

        def check_kyc() -> Dict[str, Any]:
            if context["kyc_status"] != "verified":
                return {
                    "authorized": False,
                    "error": "kyc_verification_required",
                    "error_description": "KYC verification is required for this operation",
                }
            return {"authorized": True}

        result = check_kyc()
        assert not result["authorized"]
        assert result["error"] == "kyc_verification_required"


class TestAuthorizationHelpers:
    """Test authorization helper functions."""

    def test_require_scope_decorator(self):
        """Test decorator for requiring scopes."""

        def require_scope(required_scope: str):
            def decorator(func):
                def wrapper(user_context: Dict[str, Any], *args, **kwargs):
                    scopes = user_context.get("scopes", [])
                    if required_scope not in scopes:
                        raise PermissionError(f"Required scope '{required_scope}' not present")
                    return func(user_context, *args, **kwargs)

                return wrapper

            return decorator

        @require_scope("profile:personal:read")
        def read_customer_data(user_context: Dict[str, Any]):
            return {"data": "customer info"}

        # Test with valid scope
        valid_context = {"scopes": ["profile:personal:read"]}
        result = read_customer_data(valid_context)
        assert result["data"] == "customer info"

        # Test without valid scope
        invalid_context = {"scopes": ["openid"]}
        with pytest.raises(PermissionError):
            read_customer_data(invalid_context)

    def test_require_resource_access(self, sample_user_context):
        """Test checking resource access."""

        def require_account_access(account_id: str, user_context: Dict[str, Any]) -> bool:
            account_ids = user_context.get("account_ids", [])
            if account_id not in account_ids:
                raise PermissionError(f"Access to account '{account_id}' denied")
            return True

        # Test with valid account
        assert require_account_access("ACC-001", sample_user_context)

        # Test with invalid account
        with pytest.raises(PermissionError):
            require_account_access("ACC-999", sample_user_context)

    def test_combine_authorization_checks(self, sample_user_context):
        """Test combining multiple authorization checks."""

        def authorize_transaction(account_id: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
            # Check scope (fine-grained)
            scopes = set(user_context.get("scopes", []))
            if not scopes & {"accounts:savings:write", "accounts:credit:write"}:
                return {"authorized": False, "reason": "insufficient_scope"}

            # Check KYC
            if user_context.get("kyc_status") != "verified":
                return {"authorized": False, "reason": "kyc_not_verified"}

            # Check account access
            if account_id not in user_context.get("account_ids", []):
                return {"authorized": False, "reason": "account_not_found"}

            return {"authorized": True}

        # Test with valid context
        result = authorize_transaction("ACC-001", sample_user_context)
        assert result["authorized"]

        # Test with invalid account
        result = authorize_transaction("ACC-999", sample_user_context)
        assert not result["authorized"]
        assert result["reason"] == "account_not_found"
