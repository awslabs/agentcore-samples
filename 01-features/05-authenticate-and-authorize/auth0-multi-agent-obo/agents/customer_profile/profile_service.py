# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Profile Service for managing customer profile data.

This service handles profile operations with mock data storage
and includes audit logging for all changes.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from copy import deepcopy

logger = logging.getLogger(__name__)


# Mock data store for customer profiles
MOCK_PROFILES = {
    "CUST001": {
        "customer_id": "CUST001",
        "title": "Mr",
        "first_name": "James",
        "middle_name": "Robert",
        "last_name": "Wilson",
        "date_of_birth": "1985-03-15",
        "primary_phone": "+61412345678",
        "secondary_phone": "+61298765432",
        "email": "james.wilson@example.com",
        "address": {
            "street_line_1": "123 Collins Street",
            "street_line_2": "Suite 456",
            "suburb": "Melbourne",
            "state": "VIC",
            "postcode": "3000",
            "country": "Australia",
        },
        "mailing_address": None,  # None means same as residential
        "customer_since": "2020-01-15",
        "preferred_contact_method": "email",
        "marketing_preferences": {
            "email_opt_in": True,
            "sms_opt_in": False,
            "mail_opt_in": False,
        },
        "last_updated": "2024-12-01T10:30:00Z",
        "updated_by": "system",
    },
    "CUST002": {
        "customer_id": "CUST002",
        "title": "Ms",
        "first_name": "Sarah",
        "middle_name": "Jane",
        "last_name": "Chen",
        "date_of_birth": "1992-07-22",
        "primary_phone": "+61423456789",
        "secondary_phone": None,
        "email": "sarah.chen@example.com",
        "address": {
            "street_line_1": "45 George Street",
            "street_line_2": "",
            "suburb": "Sydney",
            "state": "NSW",
            "postcode": "2000",
            "country": "Australia",
        },
        "mailing_address": {
            "street_line_1": "PO Box 789",
            "street_line_2": "",
            "suburb": "Sydney",
            "state": "NSW",
            "postcode": "2001",
            "country": "Australia",
        },
        "customer_since": "2021-06-10",
        "preferred_contact_method": "sms",
        "marketing_preferences": {
            "email_opt_in": True,
            "sms_opt_in": True,
            "mail_opt_in": True,
        },
        "last_updated": "2025-01-02T14:15:00Z",
        "updated_by": "CUST002",
    },
    "CUST003": {
        "customer_id": "CUST003",
        "title": "Dr",
        "first_name": "Michael",
        "middle_name": "",
        "last_name": "Thompson",
        "date_of_birth": "1978-11-08",
        "primary_phone": "+61434567890",
        "secondary_phone": "+61287654321",
        "email": "michael.thompson@example.com",
        "address": {
            "street_line_1": "78 Queen Street",
            "street_line_2": "Apartment 12B",
            "suburb": "Brisbane",
            "state": "QLD",
            "postcode": "4000",
            "country": "Australia",
        },
        "mailing_address": None,
        "customer_since": "2019-03-20",
        "preferred_contact_method": "phone",
        "marketing_preferences": {
            "email_opt_in": False,
            "sms_opt_in": False,
            "mail_opt_in": False,
        },
        "last_updated": "2024-11-15T09:45:00Z",
        "updated_by": "CUST003",
    },
}


class ProfileService:
    """Service for managing customer profile operations."""

    def __init__(self):
        """Initialize the profile service."""
        self.profiles = MOCK_PROFILES
        logger.info(f"ProfileService initialized with {len(self.profiles)} mock profiles")

    def get_profile(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a customer profile.

        Args:
            customer_id: The customer ID to retrieve

        Returns:
            Optional[Dict[str, Any]]: Profile data or None if not found
        """
        profile = self.profiles.get(customer_id)

        if profile:
            logger.info(f"Retrieved profile for customer_id={customer_id}")
            # Return a deep copy to prevent external modifications
            return deepcopy(profile)
        else:
            logger.warning(f"Profile not found for customer_id={customer_id}")
            return None

    def update_profile(self, customer_id: str, updates: Dict[str, Any], updated_by: str) -> Dict[str, Any]:
        """
        Update profile fields.

        Args:
            customer_id: The customer ID to update
            updates: Dictionary of field updates
            updated_by: Identifier of who made the update

        Returns:
            Dict[str, Any]: Updated profile data

        Raises:
            ValueError: If profile not found or invalid fields
        """
        profile = self.profiles.get(customer_id)

        if not profile:
            logger.error(f"Cannot update: profile not found for customer_id={customer_id}")
            raise ValueError(f"Profile not found for customer {customer_id}")

        # List of fields that cannot be updated directly
        protected_fields = {"customer_id", "customer_since", "date_of_birth"}

        # Filter out protected fields
        invalid_updates = set(updates.keys()) & protected_fields
        if invalid_updates:
            logger.warning(f"Attempt to update protected fields: {invalid_updates}")
            raise ValueError(f"Cannot update protected fields: {', '.join(invalid_updates)}")

        # Apply updates
        old_values = {}
        for field, value in updates.items():
            if field in profile:
                old_values[field] = profile[field]
                profile[field] = value

        # Update metadata
        profile["last_updated"] = datetime.utcnow().isoformat() + "Z"
        profile["updated_by"] = updated_by

        # Audit log
        self._log_audit(
            customer_id=customer_id,
            action="update_profile",
            updates=updates,
            old_values=old_values,
            updated_by=updated_by,
        )

        logger.info(f"Profile updated for customer_id={customer_id}")
        return deepcopy(profile)

    def update_address(
        self,
        customer_id: str,
        address: Dict[str, str],
        updated_by: str,
        is_mailing: bool = False,
    ) -> Dict[str, Any]:
        """
        Update customer address.

        Args:
            customer_id: The customer ID to update
            address: New address details
            updated_by: Identifier of who made the update
            is_mailing: If True, update mailing address; otherwise residential

        Returns:
            Dict[str, Any]: Updated profile data

        Raises:
            ValueError: If profile not found or invalid address
        """
        profile = self.profiles.get(customer_id)

        if not profile:
            raise ValueError(f"Profile not found for customer {customer_id}")

        # Validate address fields
        required_fields = ["street_line_1", "suburb", "state", "postcode", "country"]
        missing_fields = [f for f in required_fields if f not in address]
        if missing_fields:
            raise ValueError(f"Missing required address fields: {', '.join(missing_fields)}")

        # Update the appropriate address
        address_type = "mailing_address" if is_mailing else "address"
        old_address = profile[address_type]
        profile[address_type] = address

        # Update metadata
        profile["last_updated"] = datetime.utcnow().isoformat() + "Z"
        profile["updated_by"] = updated_by

        # Audit log
        self._log_audit(
            customer_id=customer_id,
            action=f"update_{address_type}",
            updates={"address": address},
            old_values={"address": old_address},
            updated_by=updated_by,
        )

        logger.info(f"Address updated for customer_id={customer_id}, type={address_type}")
        return deepcopy(profile)

    def update_phone(
        self,
        customer_id: str,
        phone_type: str,
        phone_number: Optional[str],
        updated_by: str,
    ) -> Dict[str, Any]:
        """
        Update customer phone number.

        Args:
            customer_id: The customer ID to update
            phone_type: Either "primary" or "secondary"
            phone_number: New phone number (None to remove)
            updated_by: Identifier of who made the update

        Returns:
            Dict[str, Any]: Updated profile data

        Raises:
            ValueError: If profile not found or invalid phone type
        """
        profile = self.profiles.get(customer_id)

        if not profile:
            raise ValueError(f"Profile not found for customer {customer_id}")

        if phone_type not in ["primary", "secondary"]:
            raise ValueError(f"Invalid phone type: {phone_type}. Must be 'primary' or 'secondary'")

        # Validate phone number format if provided
        if phone_number and not phone_number.startswith("+"):
            raise ValueError("Phone number must be in international format (e.g., +61412345678)")

        field_name = f"{phone_type}_phone"
        old_phone = profile[field_name]
        profile[field_name] = phone_number

        # Update metadata
        profile["last_updated"] = datetime.utcnow().isoformat() + "Z"
        profile["updated_by"] = updated_by

        # Audit log
        self._log_audit(
            customer_id=customer_id,
            action=f"update_{phone_type}_phone",
            updates={field_name: phone_number},
            old_values={field_name: old_phone},
            updated_by=updated_by,
        )

        logger.info(f"Phone updated for customer_id={customer_id}, type={phone_type}")
        return deepcopy(profile)

    def update_preferences(self, customer_id: str, preferences: Dict[str, Any], updated_by: str) -> Dict[str, Any]:
        """
        Update customer contact and marketing preferences.

        Args:
            customer_id: The customer ID to update
            preferences: New preference values
            updated_by: Identifier of who made the update

        Returns:
            Dict[str, Any]: Updated profile data

        Raises:
            ValueError: If profile not found or invalid preferences
        """
        profile = self.profiles.get(customer_id)

        if not profile:
            raise ValueError(f"Profile not found for customer {customer_id}")

        old_preferences = {}

        # Update preferred contact method if provided
        if "preferred_contact_method" in preferences:
            method = preferences["preferred_contact_method"]
            valid_methods = ["email", "sms", "phone", "mail"]
            if method not in valid_methods:
                raise ValueError(f"Invalid contact method: {method}. Must be one of {', '.join(valid_methods)}")
            old_preferences["preferred_contact_method"] = profile["preferred_contact_method"]
            profile["preferred_contact_method"] = method

        # Update marketing preferences if provided
        if "marketing_preferences" in preferences:
            marketing = preferences["marketing_preferences"]
            old_preferences["marketing_preferences"] = deepcopy(profile["marketing_preferences"])

            # Update individual marketing preferences
            for key in ["email_opt_in", "sms_opt_in", "mail_opt_in"]:
                if key in marketing:
                    if not isinstance(marketing[key], bool):
                        raise ValueError(f"{key} must be a boolean value")
                    profile["marketing_preferences"][key] = marketing[key]

        # Update metadata
        profile["last_updated"] = datetime.utcnow().isoformat() + "Z"
        profile["updated_by"] = updated_by

        # Audit log
        self._log_audit(
            customer_id=customer_id,
            action="update_preferences",
            updates=preferences,
            old_values=old_preferences,
            updated_by=updated_by,
        )

        logger.info(f"Preferences updated for customer_id={customer_id}")
        return deepcopy(profile)

    def _log_audit(
        self,
        customer_id: str,
        action: str,
        updates: Dict[str, Any],
        old_values: Dict[str, Any],
        updated_by: str,
    ) -> None:
        """
        Log audit trail for profile changes.

        Args:
            customer_id: The customer ID
            action: Action performed
            updates: New values
            old_values: Previous values
            updated_by: Who made the change
        """
        audit_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "customer_id": customer_id,
            "action": action,
            "updated_by": updated_by,
            "changes": {"old": old_values, "new": updates},
        }

        # In a real system, this would write to an audit log database
        logger.info(f"AUDIT: {audit_entry}")


# Global service instance
profile_service = ProfileService()
