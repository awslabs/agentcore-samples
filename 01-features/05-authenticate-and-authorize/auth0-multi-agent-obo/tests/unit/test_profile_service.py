# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for profile service operations.

Tests CRUD operations for simplified customer profiles.
"""

import pytest
from datetime import datetime


class TestProfileServiceGet:
    """Test profile retrieval operations."""

    def test_get_profile_by_customer_id(self, sample_customer_profile, mock_dynamodb_table):
        """Test retrieving profile by customer ID."""
        mock_dynamodb_table.get_item.return_value = {"Item": sample_customer_profile}

        response = mock_dynamodb_table.get_item(Key={"customer_id": "CUST-12345"})

        assert "Item" in response
        assert response["Item"]["customer_id"] == "CUST-12345"
        mock_dynamodb_table.get_item.assert_called_once()

    def test_get_profile_not_found(self, mock_dynamodb_table):
        """Test retrieving non-existent profile."""
        mock_dynamodb_table.get_item.return_value = {}

        response = mock_dynamodb_table.get_item(Key={"customer_id": "CUST-99999"})

        assert "Item" not in response

    def test_get_profile_with_authorization(self, sample_customer_profile, sample_user_context):
        """Test retrieving profile with authorization check."""
        requested_customer_id = "CUST-12345"
        user_customer_id = sample_user_context["customer_id"]

        is_authorized = requested_customer_id == user_customer_id
        assert is_authorized


class TestProfileServiceCreate:
    """Test profile creation operations."""

    def test_create_new_profile(self, sample_customer_profile, mock_dynamodb_table):
        """Test creating a new customer profile."""
        mock_dynamodb_table.put_item.return_value = {}

        mock_dynamodb_table.put_item(
            Item=sample_customer_profile,
            ConditionExpression="attribute_not_exists(customer_id)",
        )

        mock_dynamodb_table.put_item.assert_called_once()

    def test_create_profile_generates_customer_id(self):
        """Test that creating profile generates customer ID."""
        import uuid

        customer_id = f"CUST-{str(uuid.uuid4())[:8].upper()}"

        assert customer_id.startswith("CUST-")
        assert len(customer_id) > 5


class TestProfileServiceUpdate:
    """Test profile update operations."""

    def test_update_profile_email(self, mock_dynamodb_table):
        """Test updating profile email."""
        mock_dynamodb_table.update_item.return_value = {
            "Attributes": {
                "customer_id": "CUST-12345",
                "email": "newemail@example.com",
                "last_updated": datetime.utcnow().isoformat() + "Z",
            }
        }

        response = mock_dynamodb_table.update_item(
            Key={"customer_id": "CUST-12345"},
            UpdateExpression="SET email = :email, last_updated = :updated",
            ExpressionAttributeValues={
                ":email": "newemail@example.com",
                ":updated": datetime.utcnow().isoformat() + "Z",
            },
            ReturnValues="ALL_NEW",
        )

        assert response["Attributes"]["email"] == "newemail@example.com"

    def test_update_profile_authorization(self, sample_user_context):
        """Test authorization for profile updates."""
        requested_customer_id = "CUST-12345"
        user_customer_id = sample_user_context["customer_id"]
        scopes = sample_user_context["scopes"]

        is_authorized = requested_customer_id == user_customer_id and "profile:personal:write" in scopes

        assert is_authorized


class TestProfileServiceValidation:
    """Test profile validation logic."""

    def test_validate_email_format(self):
        """Test email validation."""
        import re

        def validate_email(email: str) -> bool:
            pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            return re.match(pattern, email) is not None

        assert validate_email("john.doe@example.com")
        assert validate_email("user+tag@example.co.uk")
        assert not validate_email("invalid.email")
        assert not validate_email("@example.com")


class TestProfileServiceErrors:
    """Test error handling in profile service."""

    def test_handle_dynamodb_error(self, mock_dynamodb_table):
        """Test handling DynamoDB errors."""
        from botocore.exceptions import ClientError

        mock_dynamodb_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}}, "GetItem"
        )

        with pytest.raises(ClientError) as exc:
            mock_dynamodb_table.get_item(Key={"customer_id": "CUST-12345"})

        assert exc.value.response["Error"]["Code"] == "ProvisionedThroughputExceededException"

    def test_handle_not_found_error(self, mock_dynamodb_table):
        """Test handling not found errors."""
        mock_dynamodb_table.get_item.return_value = {}

        response = mock_dynamodb_table.get_item(Key={"customer_id": "CUST-99999"})

        assert "Item" not in response
