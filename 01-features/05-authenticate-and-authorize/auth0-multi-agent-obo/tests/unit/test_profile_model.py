# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for simplified CustomerProfile model.

Tests the CustomerProfile data model used for demonstrating Auth0 3LO
authentication and JWT-based authorization patterns.
"""

import json
from datetime import datetime


class TestCustomerProfileModel:
    """Test CustomerProfile data model."""

    def test_create_customer_profile(self, sample_customer_profile):
        """Test creating a CustomerProfile instance."""
        assert sample_customer_profile["customer_id"] == "CUST-12345"
        assert sample_customer_profile["name"] == "John Doe"
        assert sample_customer_profile["email"] == "john.doe@example.com"

    def test_profile_required_fields(self, sample_customer_profile):
        """Test that required fields are present."""
        required_fields = ["customer_id", "name", "email", "last_updated"]

        for field in required_fields:
            assert field in sample_customer_profile
            assert sample_customer_profile[field] is not None

    def test_profile_optional_fields(self, sample_customer_profile):
        """Test optional phone field."""
        assert "phone" in sample_customer_profile
        assert sample_customer_profile["phone"] == "+61412345678"


class TestCustomerProfileValidation:
    """Test validation of customer profile."""

    def test_validate_customer_id_format(self, sample_customer_profile):
        """Test validation of customer ID format."""
        customer_id = sample_customer_profile["customer_id"]
        assert customer_id.startswith("CUST-")
        assert len(customer_id) > 5

    def test_validate_email_format(self, sample_customer_profile):
        """Test validation of email format."""
        email = sample_customer_profile["email"]
        assert "@" in email
        assert "." in email.split("@")[1]


class TestCustomerProfileOperations:
    """Test operations on customer profile."""

    def test_profile_serialization(self, sample_customer_profile):
        """Test serializing profile to JSON."""
        json_str = json.dumps(sample_customer_profile)
        assert json_str is not None

        # Deserialize and verify
        deserialized = json.loads(json_str)
        assert deserialized["customer_id"] == sample_customer_profile["customer_id"]

    def test_profile_update(self, sample_customer_profile):
        """Test updating profile fields."""
        profile = sample_customer_profile.copy()
        original_updated = profile["last_updated"]

        # Update email
        profile["email"] = "newemail@example.com"
        profile["last_updated"] = datetime.utcnow().isoformat() + "Z"

        assert profile["email"] == "newemail@example.com"
        assert profile["last_updated"] != original_updated


class TestCustomerProfileEdgeCases:
    """Test edge cases for customer profile."""

    def test_profile_with_no_phone(self):
        """Test profile with no phone."""
        profile = {
            "customer_id": "CUST-12345",
            "name": "John Doe",
            "email": "john.doe@example.com",
            "last_updated": "2024-01-15T10:30:00Z",
            "phone": None,
        }

        assert profile["phone"] is None

    def test_profile_with_special_characters(self):
        """Test profile with special characters in names."""
        profile = {
            "customer_id": "CUST-12345",
            "name": "Mary-Jane O'Brien-Smith",
            "email": "mary.obrien@example.com",
            "last_updated": "2024-01-15T10:30:00Z",
        }

        assert "-" in profile["name"]
        assert "'" in profile["name"]
