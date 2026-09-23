# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""User context model for AgentCore Identity."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class UserContext:
    """
    Represents the authenticated user's context extracted from JWT claims.

    This includes identity information, permissions, and metadata needed
    for authorization and personalization throughout the agent system.
    """

    user_id: str  # Auth0 sub claim
    customer_id: str  # Custom claim
    email: str
    name: str
    account_types: list[str] = field(default_factory=list)  # e.g., ["savings", "checking", "credit"]
    roles: list[str] = field(default_factory=list)  # e.g., ["customer", "premium"]
    scopes: list[str] = field(default_factory=list)
    kyc_status: str = "unknown"
    token_expiry: datetime = field(default_factory=datetime.utcnow)
    raw_claims: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_jwt_claims(
        cls,
        claims: dict[str, Any],
        claims_namespace: str = "https://agentcore.example.com/",
    ) -> "UserContext":
        """
        Construct a UserContext from JWT claims dictionary.

        Args:
            claims: The decoded JWT claims dictionary
            claims_namespace: The namespace prefix for custom claims

        Returns:
            UserContext instance populated from the claims

        Example:
            >>> claims = {
            ...     "sub": "auth0|123456",
            ...     "email": "user@example.com",
            ...     "name": "John Doe",
            ...     "https://agentcore.example.com/customer_id": "CUST-001",
            ...     "https://agentcore.example.com/account_types": ["savings", "checking"],
            ...     "https://agentcore.example.com/roles": ["customer", "premium"],
            ...     "scope": "openid profile email read:accounts",
            ...     "exp": 1704153600
            ... }
            >>> context = UserContext.from_jwt_claims(claims)
        """
        # Extract standard OIDC claims
        user_id = claims.get("sub", "")
        email = claims.get("email", "")
        name = claims.get("name", "")

        # Extract custom namespaced claims
        customer_id = claims.get(f"{claims_namespace}customer_id", "")
        account_types = claims.get(f"{claims_namespace}account_types", [])
        roles = claims.get(f"{claims_namespace}roles", [])
        kyc_status = claims.get(f"{claims_namespace}kyc_status", "unknown")

        # Extract scopes
        scope_string = claims.get("scope", "")
        scopes = scope_string.split() if isinstance(scope_string, str) else []

        # Extract token expiry
        exp_timestamp = claims.get("exp", 0)
        token_expiry = datetime.utcfromtimestamp(exp_timestamp) if exp_timestamp else datetime.utcnow()

        return cls(
            user_id=user_id,
            customer_id=customer_id,
            email=email,
            name=name,
            account_types=account_types,
            roles=roles,
            scopes=scopes,
            kyc_status=kyc_status,
            token_expiry=token_expiry,
            raw_claims=claims,
        )

    def has_scope(self, scope: str) -> bool:
        """Check if the user has a specific scope."""
        return scope in self.scopes

    def has_role(self, role: str) -> bool:
        """Check if the user has a specific role."""
        return role in self.roles

    def has_account_type(self, account_type: str) -> bool:
        """Check if the user has access to a specific account type."""
        return account_type in self.account_types

    def is_token_expired(self) -> bool:
        """Check if the token has expired."""
        return datetime.utcnow() > self.token_expiry
