# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Authentication Context Management for Coordinator Agent.

This module handles extraction and validation of user authentication context
from AgentCore requests, including JWT claims, Auth0 custom claims, and
authorization checks.
"""

import logging
from typing import Any, Dict, List, Optional

# Note: InvocationContext doesn't exist in bedrock_agentcore.runtime
# Using Any type instead for request parameter
logger = logging.getLogger(__name__)

# Custom claims namespace for Auth0
CLAIMS_NAMESPACE = "https://agentcore.example.com/"


class AuthContextManager:
    """
    Manages authentication context throughout the agent lifecycle.

    This class provides methods to extract, validate, and manage user
    authentication context from incoming requests.
    """

    def __init__(self, claims_namespace: str = CLAIMS_NAMESPACE):
        """
        Initialize the AuthContextManager.

        Args:
            claims_namespace: Namespace for custom claims (Auth0)
        """
        self.claims_namespace = claims_namespace
        self.context_cache: Dict[str, Dict[str, Any]] = {}

    def extract_context(self, request: Any) -> Dict[str, Any]:
        """
        Extract user context from an invocation request.

        Args:
            request: The invocation request containing auth claims

        Returns:
            Dictionary containing user context including:
            - user_id: Unique user identifier (Auth0 sub claim)
            - email: User email address
            - customer_id: Financial services customer ID
            - permissions: List of granted permissions/scopes
            - custom_claims: Any additional custom claims
        """
        context = {}

        try:
            # Extract from request attributes (populated by JWT authorizer)
            attributes = getattr(request, "request_attributes", {}) or {}

            # Standard OIDC claims
            context["user_id"] = attributes.get("sub") or attributes.get("user_id", "")
            context["email"] = attributes.get("email", "")
            context["email_verified"] = attributes.get("email_verified", False)

            # Extract scopes/permissions
            scopes_str = attributes.get("scope", "")
            permissions = scopes_str.split() if scopes_str else []
            context["permissions"] = permissions

            # Extract custom claims (with namespace)
            custom_claims = {}
            for key, value in attributes.items():
                if key.startswith(self.claims_namespace):
                    claim_name = key.replace(self.claims_namespace, "")
                    custom_claims[claim_name] = value

            # Customer ID from custom claims
            context["customer_id"] = custom_claims.get("customer_id") or attributes.get("customer_id", "")

            # Department/organization from custom claims
            context["department"] = custom_claims.get("department", "")
            context["organization"] = custom_claims.get("organization", "")

            # Account tier (premium, standard, basic)
            context["account_tier"] = custom_claims.get("account_tier", "standard")

            # Store all custom claims
            context["custom_claims"] = custom_claims

            # Request metadata
            context["session_id"] = getattr(request, "session_id", "")
            context["request_time"] = getattr(request, "timestamp", None)

            logger.info(
                f"Extracted user context for user_id: {context['user_id']}, customer_id: {context['customer_id']}"
            )

            # Cache the context
            if context.get("user_id"):
                self.context_cache[context["user_id"]] = context

            return context

        except Exception as e:
            logger.exception(f"Error extracting user context: {str(e)}")
            raise ValueError(f"Failed to extract user context: {str(e)}")

    def validate_authorization(self, context: Dict[str, Any], required_permissions: Optional[List[str]] = None) -> bool:
        """
        Validate that the user has the required authorization.

        Args:
            context: User context dictionary
            required_permissions: Optional list of required permissions

        Returns:
            True if authorized, False otherwise
        """
        try:
            # Check basic requirements
            if not context.get("user_id"):
                logger.warning("Authorization failed: No user_id in context")
                return False

            if not context.get("customer_id"):
                logger.warning("Authorization failed: No customer_id in context")
                return False

            # Verify email is verified (for sensitive operations)
            if not context.get("email_verified", False):
                logger.warning(f"Authorization warning: Email not verified for user {context['user_id']}")
                # Note: We still allow access, but log the warning
                # Adjust this based on your security requirements

            # Check required permissions if specified
            if required_permissions:
                user_permissions = set(context.get("permissions", []))
                required_set = set(required_permissions)

                if not required_set.issubset(user_permissions):
                    missing = required_set - user_permissions
                    logger.warning(f"Authorization failed: Missing permissions {missing} for user {context['user_id']}")
                    return False

            logger.info(f"Authorization validated for user {context['user_id']}")
            return True

        except Exception as e:
            logger.exception(f"Error validating authorization: {str(e)}")
            return False

    def get_cached_context(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached context for a user.

        Args:
            user_id: User identifier

        Returns:
            Cached context or None if not found
        """
        return self.context_cache.get(user_id)

    def clear_cache(self, user_id: Optional[str] = None) -> None:
        """
        Clear cached context.

        Args:
            user_id: Optional user_id to clear. If None, clears all cache.
        """
        if user_id:
            self.context_cache.pop(user_id, None)
            logger.info(f"Cleared context cache for user {user_id}")
        else:
            self.context_cache.clear()
            logger.info("Cleared all context cache")

    def enrich_context(self, context: Dict[str, Any], additional_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enrich context with additional data.

        Args:
            context: Base user context
            additional_data: Additional data to merge

        Returns:
            Enriched context dictionary
        """
        enriched = context.copy()
        enriched.update(additional_data)
        return enriched


# Global auth context manager instance
_auth_manager = AuthContextManager()


def extract_user_context(request: Any) -> Dict[str, Any]:
    """
    Extract user context from an invocation request.

    This is a convenience function that uses the global AuthContextManager.

    Args:
        request: The invocation request containing auth claims

    Returns:
        Dictionary containing user context
    """
    return _auth_manager.extract_context(request)


def validate_user_authorization(context: Dict[str, Any], required_permissions: Optional[List[str]] = None) -> bool:
    """
    Validate that the user has the required authorization.

    This is a convenience function that uses the global AuthContextManager.

    Args:
        context: User context dictionary
        required_permissions: Optional list of required permissions

    Returns:
        True if authorized, False otherwise
    """
    # For demo/testing: If user has valid JWT (user_id present), allow access
    # AgentCore's customJWTAuthorizer already validated the token
    user_id = context.get("user_id", "unknown")
    if user_id and user_id != "unknown":
        # User has been authenticated via JWT
        logger.info(f"Authorization granted for authenticated user: {user_id}")
        # Still validate with permissions if specified, but log if missing
        if required_permissions:
            return _auth_manager.validate_authorization(context, required_permissions)
        # Default: allow authenticated users
        return True

    # No valid user_id - require full permission check
    default_permissions = ["profile:personal:read"]
    permissions = required_permissions or default_permissions
    return _auth_manager.validate_authorization(context, permissions)


def get_customer_id(context: Dict[str, Any]) -> str:
    """
    Extract customer ID from context.

    Args:
        context: User context dictionary

    Returns:
        Customer ID string

    Raises:
        ValueError: If customer_id is not present
    """
    customer_id = context.get("customer_id")
    if not customer_id:
        raise ValueError("Customer ID not found in context")
    return customer_id


def get_user_permissions(context: Dict[str, Any]) -> List[str]:
    """
    Get list of user permissions from context.

    Args:
        context: User context dictionary

    Returns:
        List of permission strings
    """
    return context.get("permissions", [])


def has_permission(context: Dict[str, Any], permission: str) -> bool:
    """
    Check if user has a specific permission.

    Args:
        context: User context dictionary
        permission: Permission string to check

    Returns:
        True if user has the permission, False otherwise
    """
    permissions = get_user_permissions(context)
    return permission in permissions


def is_premium_customer(context: Dict[str, Any]) -> bool:
    """
    Check if user is a premium customer.

    Args:
        context: User context dictionary

    Returns:
        True if premium customer, False otherwise
    """
    return context.get("account_tier", "").lower() == "premium"
