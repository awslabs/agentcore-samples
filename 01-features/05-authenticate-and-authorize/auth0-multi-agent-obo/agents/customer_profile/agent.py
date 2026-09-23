# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Customer Profile Agent - Simple Implementation.

This is a simplified version that doesn't use strands Agent framework,
to debug container issues. Uses direct profile_service calls instead.
"""

import logging
from typing import Any, Dict, Optional
from profile_service import profile_service

logger = logging.getLogger(__name__)


class CustomerProfileAgent:
    """
    Customer Profile Agent - Simple Implementation.

    Uses direct profile_service calls instead of strands Agent.
    """

    def __init__(self, user_id: Optional[str] = None, customer_id: Optional[str] = None):
        """
        Initialize the Customer Profile Agent.

        Args:
            user_id: The authenticated user's ID from JWT claims
            customer_id: The customer ID for authorization
        """
        self.user_id = user_id
        self.customer_id = customer_id or user_id

        logger.info(f"CustomerProfileAgent initialized for user={user_id}, customer={customer_id}")

    def process_query(self, query: str) -> Dict[str, Any]:
        """
        Process a customer profile query.

        Args:
            query: Natural language query about customer profile

        Returns:
            Response dict with profile data or error information
        """
        logger.info(f"Processing profile query: {query[:100]}")

        try:
            query_lower = query.lower()

            # Simple query routing based on keywords
            if "profile" in query_lower or "show" in query_lower or "view" in query_lower:
                return self._get_profile()
            elif "address" in query_lower:
                return self._get_address_info()
            elif "phone" in query_lower:
                return self._get_phone_info()
            elif "preference" in query_lower:
                return self._get_preferences()
            else:
                # Default to showing profile
                return self._get_profile()

        except Exception as e:
            logger.error(f"Error processing profile query: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "message": "Failed to process profile query",
            }

    def _get_profile(self) -> Dict[str, Any]:
        """Get full customer profile."""
        # Use mock customer ID mapping for testing
        profile_customer_id = self._map_customer_id()

        profile = profile_service.get_profile(profile_customer_id)

        if profile:
            # Format profile for display
            formatted = self._format_profile(profile)
            return {
                "status": "success",
                "response": formatted,
                "data": profile,
                "agent": "customer_profile_agent",
                "customer_id": self.customer_id,
            }
        else:
            return {
                "status": "error",
                "error": "PROFILE_NOT_FOUND",
                "message": f"No profile found for customer {profile_customer_id}",
            }

    def _get_address_info(self) -> Dict[str, Any]:
        """Get customer address information."""
        profile_customer_id = self._map_customer_id()
        profile = profile_service.get_profile(profile_customer_id)

        if profile:
            address = profile.get("address", {})
            mailing = profile.get("mailing_address")

            response_text = "**Residential Address:**\n"
            response_text += f"{address.get('street_line_1', '')}\n"
            if address.get("street_line_2"):
                response_text += f"{address['street_line_2']}\n"
            response_text += f"{address.get('suburb', '')} {address.get('state', '')} {address.get('postcode', '')}\n"
            response_text += f"{address.get('country', '')}\n"

            if mailing:
                response_text += "\n**Mailing Address:**\n"
                response_text += f"{mailing.get('street_line_1', '')}\n"
                if mailing.get("street_line_2"):
                    response_text += f"{mailing['street_line_2']}\n"
                response_text += (
                    f"{mailing.get('suburb', '')} {mailing.get('state', '')} {mailing.get('postcode', '')}\n"
                )

            return {
                "status": "success",
                "response": response_text,
                "agent": "customer_profile_agent",
                "customer_id": self.customer_id,
            }
        else:
            return {
                "status": "error",
                "error": "PROFILE_NOT_FOUND",
                "message": "No profile found for customer",
            }

    def _get_phone_info(self) -> Dict[str, Any]:
        """Get customer phone information."""
        profile_customer_id = self._map_customer_id()
        profile = profile_service.get_profile(profile_customer_id)

        if profile:
            response_text = "**Contact Numbers:**\n"
            response_text += f"Primary: {profile.get('primary_phone', 'Not set')}\n"
            secondary = profile.get("secondary_phone")
            response_text += f"Secondary: {secondary if secondary else 'Not set'}\n"

            return {
                "status": "success",
                "response": response_text,
                "agent": "customer_profile_agent",
                "customer_id": self.customer_id,
            }
        else:
            return {
                "status": "error",
                "error": "PROFILE_NOT_FOUND",
                "message": "No profile found for customer",
            }

    def _get_preferences(self) -> Dict[str, Any]:
        """Get customer preferences."""
        profile_customer_id = self._map_customer_id()
        profile = profile_service.get_profile(profile_customer_id)

        if profile:
            prefs = profile.get("marketing_preferences", {})
            response_text = "**Contact Preferences:**\n"
            response_text += f"Preferred contact method: {profile.get('preferred_contact_method', 'Not set')}\n"
            response_text += "\n**Marketing Preferences:**\n"
            response_text += f"Email opt-in: {'Yes' if prefs.get('email_opt_in') else 'No'}\n"
            response_text += f"SMS opt-in: {'Yes' if prefs.get('sms_opt_in') else 'No'}\n"
            response_text += f"Mail opt-in: {'Yes' if prefs.get('mail_opt_in') else 'No'}\n"

            return {
                "status": "success",
                "response": response_text,
                "agent": "customer_profile_agent",
                "customer_id": self.customer_id,
            }
        else:
            return {
                "status": "error",
                "error": "PROFILE_NOT_FOUND",
                "message": "No profile found for customer",
            }

    def _map_customer_id(self) -> str:
        """Map incoming customer_id to mock profile ID."""
        # For testing, map any customer ID to CUST001
        # In production, this would be a direct lookup
        if self.customer_id in ["CUST001", "CUST002", "CUST003"]:
            return self.customer_id
        return "CUST001"  # Default for testing

    def _format_profile(self, profile: Dict[str, Any]) -> str:
        """Format profile data for display."""
        name = f"{profile.get('title', '')} {profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
        address = profile.get("address", {})

        formatted = f"""**Customer Profile**

**Name:** {name}
**Email:** {profile.get("email", "Not set")}
**Primary Phone:** {profile.get("primary_phone", "Not set")}
**Customer Since:** {profile.get("customer_since", "Unknown")}

**Address:**
{address.get("street_line_1", "")}
{address.get("street_line_2", "") if address.get("street_line_2") else ""}
{address.get("suburb", "")} {address.get("state", "")} {address.get("postcode", "")}
{address.get("country", "")}

**Preferred Contact:** {profile.get("preferred_contact_method", "Not set")}
"""
        return formatted.strip()


def create_agent(user_id: Optional[str] = None, customer_id: Optional[str] = None) -> CustomerProfileAgent:
    """
    Create and configure the Customer Profile Agent.

    Args:
        user_id: The authenticated user's ID from JWT claims
        customer_id: The customer ID for authorization

    Returns:
        CustomerProfileAgent: Configured agent with process_query method
    """
    return CustomerProfileAgent(user_id=user_id, customer_id=customer_id)
