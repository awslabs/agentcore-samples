# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Customer profile models for AgentCore Identity.

Simplified model focused on demonstrating Auth0 3LO authentication
and JWT-based authorization patterns.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional


@dataclass
class CustomerProfile:
    """
    Represents a customer's profile information.

    This is a simplified model focused on demonstrating authentication
    and authorization patterns with AWS AgentCore and Auth0.
    """

    customer_id: str
    name: str
    email: str
    last_updated: datetime
    phone: Optional[str] = None

    @property
    def display_name(self) -> str:
        """Get a display-friendly name."""
        return self.name or self.email

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the profile to a dictionary for serialization.

        Returns:
            Dictionary representation with datetime as ISO string
        """
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CustomerProfile":
        """
        Create a CustomerProfile from a dictionary.

        Args:
            data: Dictionary containing profile data

        Returns:
            CustomerProfile instance
        """
        profile_data = data.copy()

        # Convert datetime string to datetime object
        if isinstance(profile_data.get("last_updated"), str):
            profile_data["last_updated"] = datetime.fromisoformat(profile_data["last_updated"])

        return cls(**profile_data)
